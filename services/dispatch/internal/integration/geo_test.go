//go:build integration

package integration_test

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

func startRedisContainer(ctx context.Context, t *testing.T) *redis.Client {
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

// Ho Chi Minh City reference points used as fixtures (lat, lng):
//   origin      : 10.762622, 106.660172  (Ben Thanh area)
//   nearDriver  : 10.763000, 106.661000  (~130 m from origin — nearest)
//   farDriver   : 10.800000, 106.700000  (~6 km from origin)
//   staleFarDriver: 10.780000, 106.680000 (~3 km, but no heartbeat → excluded)

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

// TestGeoDriverMatching verifies that MatchDriver returns the nearest available
// (fresh-heartbeat) driver and correctly excludes stale and rejected drivers.
func TestGeoDriverMatching(t *testing.T) {
	ctx := context.Background()
	rdb := startRedisContainer(ctx, t)

	dispatchSvc := service.NewDispatchService(rdb)

	// Coords for the trip origin
	const (
		originLat = 10.762622
		originLng = 106.660172
	)

	nearDriverID := "driver-near-001"
	farDriverID := "driver-far-002"
	staleDriverID := "driver-stale-003"

	t.Run("nearest fresh driver is returned first", func(t *testing.T) {
		rdb.FlushDB(ctx) // clean state for each subtest

		seedDriver(ctx, t, rdb, nearDriverID, 10.763000, 106.661000, true)
		seedDriver(ctx, t, rdb, farDriverID, 10.800000, 106.700000, true)
		seedDriver(ctx, t, rdb, staleDriverID, 10.780000, 106.680000, false) // no heartbeat

		matched, err := dispatchSvc.MatchDriver(ctx, "trip-001", originLat, originLng)
		require.NoError(t, err)
		assert.Equal(t, nearDriverID, matched, "should return the nearest driver with a valid heartbeat")
	})

	t.Run("stale driver is excluded even when geographically closest", func(t *testing.T) {
		rdb.FlushDB(ctx)

		// Only stale driver at near position — should be excluded
		seedDriver(ctx, t, rdb, staleDriverID, 10.763000, 106.661000, false)
		// Fresh driver further away
		seedDriver(ctx, t, rdb, farDriverID, 10.800000, 106.700000, true)

		matched, err := dispatchSvc.MatchDriver(ctx, "trip-002", originLat, originLng)
		require.NoError(t, err)
		assert.Equal(t, farDriverID, matched, "stale driver should be skipped in favour of fresh far driver")
	})

	t.Run("rejected driver is skipped and next-best is returned", func(t *testing.T) {
		rdb.FlushDB(ctx)

		seedDriver(ctx, t, rdb, nearDriverID, 10.763000, 106.661000, true)
		seedDriver(ctx, t, rdb, farDriverID, 10.800000, 106.700000, true)

		const tripID = "trip-003"
		// Record rejection for the nearest driver
		require.NoError(t, dispatchSvc.RecordRejection(ctx, tripID, nearDriverID))

		matched, err := dispatchSvc.MatchDriver(ctx, tripID, originLat, originLng)
		require.NoError(t, err)
		assert.Equal(t, farDriverID, matched, "rejected driver should be excluded, falling back to far driver")
	})

	t.Run("no drivers available returns empty string", func(t *testing.T) {
		rdb.FlushDB(ctx)
		// Seed a stale-only driver — no fresh ones
		seedDriver(ctx, t, rdb, staleDriverID, 10.763000, 106.661000, false)

		matched, err := dispatchSvc.MatchDriver(ctx, "trip-004", originLat, originLng)
		require.NoError(t, err)
		assert.Empty(t, matched, "should return empty string when no fresh drivers are available")
	})
}
