//go:build integration

package geo_test

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

// Ho Chi Minh City reference points used as fixtures (lat, lng):
//
//	origin        : 10.762622, 106.660172  (Ben Thanh area)
//	nearDriver    : 10.763000, 106.661000  (~130 m from origin — nearest)
//	farDriver     : 10.800000, 106.700000  (~6 km from origin)
//	staleDriver   : 10.780000, 106.680000  (~3 km, but no heartbeat → excluded)
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

// TestGeoDriverMatching verifies that MatchDriver returns the nearest available
// (fresh-heartbeat) driver and correctly excludes stale and rejected drivers.
func TestGeoDriverMatching(t *testing.T) {
	logBegin(t, "TestGeoDriverMatching",
		"MatchDriver returns nearest fresh driver; excludes stale/reserved/rejected")

	ctx := context.Background()
	rdb := startRedis(ctx, t)
	dispatchSvc := service.NewDispatchService(rdb)

	const (
		originLat = 10.762622
		originLng = 106.660172
	)

	nearDriverID := "driver-near-001"
	farDriverID := "driver-far-002"
	staleDriverID := "driver-stale-003"

	t.Run("nearest fresh driver is returned first", func(t *testing.T) {
		logStep(t, 1, 4, "seed 3 drivers (near-fresh, far-fresh, near-stale)")
		rdb.FlushDB(ctx)

		logArrow(t, "seeding near-fresh driver at 10.763000, 106.661000")
		seedDriver(ctx, t, rdb, nearDriverID, 10.763000, 106.661000, true)
		logArrow(t, "seeding far-fresh driver at 10.800000, 106.700000")
		seedDriver(ctx, t, rdb, farDriverID, 10.800000, 106.700000, true)
		logArrow(t, "seeding near-stale driver at 10.780000, 106.680000 (no heartbeat)")
		seedDriver(ctx, t, rdb, staleDriverID, 10.780000, 106.680000, false)

		logDetail(t, "origin", fmt.Sprintf("%.6f, %.6f", originLat, originLng))
		logArrow(t, "calling MatchDriver for trip-001")
		matched, err := dispatchSvc.MatchDriver(ctx, "trip-001", originLat, originLng)
		require.NoError(t, err)
		assert.Equal(t, nearDriverID, matched, "should return the nearest driver with a valid heartbeat")

		logResult(t, fmt.Sprintf("✓ matched driver: %s", matched))
	})

	t.Run("stale driver is excluded even when geographically closest", func(t *testing.T) {
		logStep(t, 2, 4, "stale driver at near position, fresh driver far away")
		rdb.FlushDB(ctx)

		logArrow(t, "seeding near-stale driver at 10.763000, 106.661000 (no heartbeat)")
		seedDriver(ctx, t, rdb, staleDriverID, 10.763000, 106.661000, false)
		logArrow(t, "seeding far-fresh driver at 10.800000, 106.700000")
		seedDriver(ctx, t, rdb, farDriverID, 10.800000, 106.700000, true)

		logArrow(t, "calling MatchDriver for trip-002")
		matched, err := dispatchSvc.MatchDriver(ctx, "trip-002", originLat, originLng)
		require.NoError(t, err)
		assert.Equal(t, farDriverID, matched, "stale driver should be skipped in favour of fresh far driver")

		logResult(t, fmt.Sprintf("✓ matched driver: %s (stale near-driver correctly excluded)", matched))
	})

	t.Run("rejected driver is skipped and next-best is returned", func(t *testing.T) {
		logStep(t, 3, 4, "record rejection for nearest; expect fallback to far driver")
		rdb.FlushDB(ctx)

		logArrow(t, "seeding near-fresh driver and far-fresh driver")
		seedDriver(ctx, t, rdb, nearDriverID, 10.763000, 106.661000, true)
		seedDriver(ctx, t, rdb, farDriverID, 10.800000, 106.700000, true)

		const tripID = "trip-003"
		logArrow(t, fmt.Sprintf("recording rejection for %s on trip %s", nearDriverID, tripID))
		require.NoError(t, dispatchSvc.RecordRejection(ctx, tripID, nearDriverID))

		logArrow(t, "calling MatchDriver — expect fallback to far driver")
		matched, err := dispatchSvc.MatchDriver(ctx, tripID, originLat, originLng)
		require.NoError(t, err)
		assert.Equal(t, farDriverID, matched, "rejected driver should be excluded, falling back to far driver")

		logResult(t, fmt.Sprintf("✓ matched driver: %s (rejected near-driver skipped)", matched))
	})

	t.Run("no drivers available returns empty string", func(t *testing.T) {
		logStep(t, 4, 4, "only stale driver seeded; expect empty match")
		rdb.FlushDB(ctx)

		logArrow(t, "seeding only stale driver (no heartbeat)")
		seedDriver(ctx, t, rdb, staleDriverID, 10.763000, 106.661000, false)

		logArrow(t, "calling MatchDriver for trip-004 — expect empty result")
		matched, err := dispatchSvc.MatchDriver(ctx, "trip-004", originLat, originLng)
		require.NoError(t, err)
		assert.Empty(t, matched, "should return empty string when no fresh drivers are available")

		logResult(t, fmt.Sprintf("✓ matched driver: %q (correctly empty — no fresh drivers)", matched))
	})
}
