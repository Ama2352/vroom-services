//go:build integration

package integration_test

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"vroom-mvp/dispatch/internal/service"
)

const (
	testOriginLat = 10.762622
	testOriginLng = 106.660172
	testDriverLat = 10.763000
	testDriverLng = 106.661000
)

// TestDispatchDriverStatusSagaCompensation verifies the Saga step 2 + compensation:
//  1. Driver is matchable when available.
//  2. ReserveDriver commits ON_OFFER — driver is no longer matchable.
//  3. ReleaseDriver (compensation) removes the reservation — driver is matchable again.
func TestDispatchDriverStatusSagaCompensation(t *testing.T) {
	ctx := context.Background()
	rdb := startRedisContainer(ctx, t)
	svc := service.NewDispatchService(rdb)

	driverID := "saga-driver-001"
	tripID := "saga-trip-001"

	// Seed driver in geo index with fresh heartbeat
	seedDriver(ctx, t, rdb, driverID, testDriverLat, testDriverLng, true)

	// Step 1: driver is available — MatchDriver should return it
	matched, err := svc.MatchDriver(ctx, tripID, testOriginLat, testOriginLng)
	require.NoError(t, err)
	assert.Equal(t, driverID, matched, "driver should be matched before reservation")

	// Step 2: Saga step 2 — commit ON_OFFER reservation
	require.NoError(t, svc.ReserveDriver(ctx, driverID))

	// Driver must be excluded from matching while reserved
	matched2, err := svc.MatchDriver(ctx, "another-trip", testOriginLat, testOriginLng)
	require.NoError(t, err)
	assert.Empty(t, matched2, "reserved driver must not be matched")

	// Verify the Redis key exists
	status, err := rdb.Get(ctx, "driver_status:"+driverID).Result()
	require.NoError(t, err)
	assert.Equal(t, "ON_OFFER", status)

	// Step 3: Saga compensation — release reservation
	require.NoError(t, svc.ReleaseDriver(ctx, driverID))

	// Driver is matchable again after release
	matched3, err := svc.MatchDriver(ctx, tripID, testOriginLat, testOriginLng)
	require.NoError(t, err)
	assert.Equal(t, driverID, matched3, "driver should be matchable again after compensation")
}

// TestDispatchOnTripTransition verifies Saga step 4 (ON_OFFER → ON_TRIP) and cleanup:
//  1. Reserve driver (ON_OFFER).
//  2. ConfirmDriverOnTrip transitions to ON_TRIP — driver still excluded from matching.
//  3. ReleaseDriver cleanup — driver available again.
func TestDispatchOnTripTransition(t *testing.T) {
	ctx := context.Background()
	rdb := startRedisContainer(ctx, t)
	svc := service.NewDispatchService(rdb)

	driverID := "saga-driver-002"

	seedDriver(ctx, t, rdb, driverID, testDriverLat, testDriverLng, true)

	// Step 1: Reserve (ON_OFFER)
	require.NoError(t, svc.ReserveDriver(ctx, driverID))

	// Step 2: Accept — transition ON_OFFER → ON_TRIP
	require.NoError(t, svc.ConfirmDriverOnTrip(ctx, driverID))

	statusVal, err := rdb.Get(ctx, "driver_status:"+driverID).Result()
	require.NoError(t, err)
	assert.Equal(t, "ON_TRIP", statusVal, "driver should be ON_TRIP after confirmation")

	// Driver still excluded while ON_TRIP
	matched, err := svc.MatchDriver(ctx, "some-trip", testOriginLat, testOriginLng)
	require.NoError(t, err)
	assert.Empty(t, matched, "ON_TRIP driver must not be matched")

	// Step 3: Trip completed — release
	require.NoError(t, svc.ReleaseDriver(ctx, driverID))

	// Driver available again
	matched2, err := svc.MatchDriver(ctx, "next-trip", testOriginLat, testOriginLng)
	require.NoError(t, err)

	// The heartbeat TTL (30s) is still live, so driver should be matchable
	assert.Equal(t, driverID, matched2, "driver should be available after trip completion")

	// Verify Redis key is gone
	exists, err := rdb.Exists(ctx, "driver_status:"+driverID).Result()
	require.NoError(t, err)
	assert.Equal(t, int64(0), exists, "driver_status key must be deleted after release")

	// Verify freshness key TTL is still reasonable (not affected by release)
	ttl, err := rdb.TTL(ctx, "driver_last_seen:"+driverID).Result()
	require.NoError(t, err)
	assert.Greater(t, ttl, time.Duration(0), "heartbeat key must still be alive")
}
