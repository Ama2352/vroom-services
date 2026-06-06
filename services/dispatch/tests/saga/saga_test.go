//go:build integration

package saga_test

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"

	"vroom-mvp/dispatch/internal/service"
)

// ─── Logger helpers ──────────────────────────────────────────────────────────

func logBegin(t *testing.T, testName, proves string) {
	t.Helper()
	t.Log("╔══════════════════════════════════════════════════════════════════╗")
	t.Logf("║  TEST    %-56s║", testName)
	t.Logf("║  PROVING %-56s║", proves)
	t.Log("╚══════════════════════════════════════════════════════════════════╝")
	t.Log("")
}

func logStep(t *testing.T, n, total int, label string) {
	t.Helper()
	t.Logf("[STEP %d/%d] %s", n, total, label)
}

func logArrow(t *testing.T, action string) {
	t.Helper()
	t.Logf("           → %s", action)
}

func logDetail(t *testing.T, key, value string) {
	t.Helper()
	t.Logf("           %-12s: %s", key, value)
}

func logResult(t *testing.T, checks ...string) {
	t.Helper()
	t.Log("")
	t.Log("[RESULT]")
	for _, c := range checks {
		t.Logf("         %s", c)
	}
}

// ─── Redis container helper ───────────────────────────────────────────────────

func startRedis(ctx context.Context, t *testing.T) *redis.Client {
	t.Helper()
	redisC, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			Image:        "redis:7-alpine",
			ExposedPorts: []string{"6379/tcp"},
			WaitingFor:   wait.ForLog("Ready to accept connections"),
		},
		Started: true,
	})
	require.NoError(t, err)
	t.Cleanup(func() { redisC.Terminate(ctx) })
	host, err := redisC.Host(ctx)
	require.NoError(t, err)
	port, err := redisC.MappedPort(ctx, "6379")
	require.NoError(t, err)
	rdb := redis.NewClient(&redis.Options{Addr: fmt.Sprintf("%s:%s", host, port.Port())})
	t.Cleanup(func() { rdb.Close() })
	require.Eventually(t, func() bool { return rdb.Ping(ctx).Err() == nil },
		15*time.Second, 300*time.Millisecond, "redis not ready")
	return rdb
}

// ─── Fixtures ────────────────────────────────────────────────────────────────

const (
	testOriginLat = 10.762622
	testOriginLng = 106.660172
	testDriverLat = 10.763000
	testDriverLng = 106.661000
)

func seedDriver(ctx context.Context, t *testing.T, rdb *redis.Client, driverID string, lat, lng float64, withHeartbeat bool) {
	t.Helper()
	err := rdb.GeoAdd(ctx, "drivers_location", &redis.GeoLocation{
		Name:      driverID,
		Latitude:  lat,
		Longitude: lng,
	}).Err()
	require.NoError(t, err)

	if withHeartbeat {
		err = rdb.Set(ctx, "driver_last_seen:"+driverID, "active", 30*time.Second).Err()
		require.NoError(t, err)
	}
}

// ─── Tests ────────────────────────────────────────────────────────────────────

// TestSaga_DriverReservationCompensation verifies the Saga step 2 + compensation:
//  1. Driver is matchable when available.
//  2. ReserveDriver commits ON_OFFER — driver is no longer matchable.
//  3. ReleaseDriver (compensation) removes the reservation — driver is matchable again.
func TestSaga_DriverReservationCompensation(t *testing.T) {
	logBegin(t, "TestSaga_DriverReservationCompensation",
		"ReserveDriver → ON_OFFER excludes from matching; ReleaseDriver restores pool")

	ctx := context.Background()
	rdb := startRedis(ctx, t)
	svc := service.NewDispatchService(rdb)

	driverID := "saga-driver-001"
	tripID := "saga-trip-001"

	logDetail(t, "driverID", driverID)
	logDetail(t, "tripID", tripID)

	// Step 1
	logStep(t, 1, 3, "Seed driver; verify MatchDriver returns it")
	logArrow(t, fmt.Sprintf("seeding driver at %.6f, %.6f with heartbeat", testDriverLat, testDriverLng))
	seedDriver(ctx, t, rdb, driverID, testDriverLat, testDriverLng, true)

	logArrow(t, "calling MatchDriver — expect driver returned before reservation")
	matched, err := svc.MatchDriver(ctx, tripID, testOriginLat, testOriginLng)
	require.NoError(t, err)
	assert.Equal(t, driverID, matched, "driver should be matched before reservation")

	logResult(t, fmt.Sprintf("✓ matched driver: %s (available in pool)", matched))

	// Step 2
	logStep(t, 2, 3, "ReserveDriver → ON_OFFER; verify excluded from matching")
	logArrow(t, fmt.Sprintf("calling ReserveDriver for %s", driverID))
	require.NoError(t, svc.ReserveDriver(ctx, driverID))

	logArrow(t, "calling MatchDriver for a different trip — expect no match")
	matched2, err := svc.MatchDriver(ctx, "another-trip", testOriginLat, testOriginLng)
	require.NoError(t, err)
	assert.Empty(t, matched2, "reserved driver must not be matched")

	status, err := rdb.Get(ctx, "driver_status:"+driverID).Result()
	require.NoError(t, err)
	assert.Equal(t, "ON_OFFER", status)

	logResult(t,
		fmt.Sprintf("✓ MatchDriver returned: %q (correctly empty)", matched2),
		fmt.Sprintf("✓ driver_status:%s = %q", driverID, status),
	)

	// Step 3
	logStep(t, 3, 3, "ReleaseDriver compensation; verify driver re-enters pool")
	logArrow(t, fmt.Sprintf("calling ReleaseDriver for %s (saga compensation)", driverID))
	require.NoError(t, svc.ReleaseDriver(ctx, driverID))

	logArrow(t, "calling MatchDriver — expect driver available again")
	matched3, err := svc.MatchDriver(ctx, tripID, testOriginLat, testOriginLng)
	require.NoError(t, err)
	assert.Equal(t, driverID, matched3, "driver should be matchable again after compensation")

	logResult(t, fmt.Sprintf("✓ matched driver: %s (re-entered pool after compensation)", matched3))
}

// TestSaga_OnTripTransition verifies Saga step 4 (ON_OFFER → ON_TRIP) and cleanup:
//  1. Reserve driver (ON_OFFER).
//  2. ConfirmDriverOnTrip transitions to ON_TRIP — driver still excluded from matching.
//  3. ReleaseDriver cleanup — driver available again.
//  4. Verify driver_status key deleted; heartbeat key still alive.
func TestSaga_OnTripTransition(t *testing.T) {
	logBegin(t, "TestSaga_OnTripTransition",
		"ON_OFFER → ON_TRIP → Released; heartbeat unaffected")

	ctx := context.Background()
	rdb := startRedis(ctx, t)
	svc := service.NewDispatchService(rdb)

	driverID := "saga-driver-002"

	logDetail(t, "driverID", driverID)
	logArrow(t, fmt.Sprintf("seeding driver at %.6f, %.6f with heartbeat", testDriverLat, testDriverLng))
	seedDriver(ctx, t, rdb, driverID, testDriverLat, testDriverLng, true)

	// Step 1
	logStep(t, 1, 4, "Reserve driver (ON_OFFER)")
	logArrow(t, fmt.Sprintf("calling ReserveDriver for %s", driverID))
	require.NoError(t, svc.ReserveDriver(ctx, driverID))
	logResult(t, fmt.Sprintf("✓ driver %s reserved → ON_OFFER", driverID))

	// Step 2
	logStep(t, 2, 4, "ConfirmDriverOnTrip → ON_TRIP; driver still excluded")
	logArrow(t, fmt.Sprintf("calling ConfirmDriverOnTrip for %s", driverID))
	require.NoError(t, svc.ConfirmDriverOnTrip(ctx, driverID))

	statusVal, err := rdb.Get(ctx, "driver_status:"+driverID).Result()
	require.NoError(t, err)
	assert.Equal(t, "ON_TRIP", statusVal, "driver should be ON_TRIP after confirmation")

	logArrow(t, "calling MatchDriver — expect no match while ON_TRIP")
	matched, err := svc.MatchDriver(ctx, "some-trip", testOriginLat, testOriginLng)
	require.NoError(t, err)
	assert.Empty(t, matched, "ON_TRIP driver must not be matched")

	logResult(t,
		fmt.Sprintf("✓ driver_status:%s = %q", driverID, statusVal),
		fmt.Sprintf("✓ MatchDriver returned: %q (correctly excluded)", matched),
	)

	// Step 3
	logStep(t, 3, 4, "ReleaseDriver; driver re-enters pool")
	logArrow(t, fmt.Sprintf("calling ReleaseDriver for %s (trip completed)", driverID))
	require.NoError(t, svc.ReleaseDriver(ctx, driverID))

	logArrow(t, "calling MatchDriver for next-trip — expect driver available")
	matched2, err := svc.MatchDriver(ctx, "next-trip", testOriginLat, testOriginLng)
	require.NoError(t, err)
	assert.Equal(t, driverID, matched2, "driver should be available after trip completion")

	logResult(t, fmt.Sprintf("✓ matched driver: %s (available after release)", matched2))

	// Step 4
	logStep(t, 4, 4, "Verify driver_status key deleted; heartbeat key still alive")
	logArrow(t, fmt.Sprintf("checking driver_status:%s key existence", driverID))
	exists, err := rdb.Exists(ctx, "driver_status:"+driverID).Result()
	require.NoError(t, err)
	assert.Equal(t, int64(0), exists, "driver_status key must be deleted after release")

	logArrow(t, fmt.Sprintf("checking driver_last_seen:%s TTL", driverID))
	ttl, err := rdb.TTL(ctx, "driver_last_seen:"+driverID).Result()
	require.NoError(t, err)
	assert.Greater(t, ttl, time.Duration(0), "heartbeat key must still be alive")

	logResult(t,
		fmt.Sprintf("✓ driver_status key exists: %d (correctly gone)", exists),
		fmt.Sprintf("✓ driver_last_seen TTL: %s (heartbeat unaffected)", ttl),
	)
}
