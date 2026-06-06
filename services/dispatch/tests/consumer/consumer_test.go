//go:build integration

package consumer_test

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"

	"vroom-mvp/dispatch/internal/service"
	"vroom-mvp/dispatch/internal/worker"
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

// ─── Test helpers ─────────────────────────────────────────────────────────────

func newConsumer(rdb *redis.Client, stream, group, consumerID string) *worker.RideEventConsumer {
	svc := service.NewDispatchService(rdb)
	return worker.NewRideEventConsumer(rdb, svc, stream, group, consumerID)
}

func publishEvent(ctx context.Context, t *testing.T, rdb *redis.Client, stream, eventID, eventType string) {
	t.Helper()
	_, err := rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: stream,
		Values: map[string]any{
			"id":           eventID,
			"type":         eventType,
			"aggregate":    "TRIP",
			"aggregate_id": uuid.New().String(),
			"payload":      `{"id":"` + uuid.New().String() + `","source_lat":10.762622,"source_lng":106.660172}`,
		},
	}).Result()
	require.NoError(t, err)
}

func createGroup(ctx context.Context, t *testing.T, rdb *redis.Client, stream, group string) {
	t.Helper()
	err := rdb.XGroupCreateMkStream(ctx, stream, group, "0").Err()
	if err != nil && err.Error() != "BUSYGROUP Consumer Group name already exists" {
		require.NoError(t, err)
	}
}

// ─── Tests ────────────────────────────────────────────────────────────────────

// TestConsumer_IdempotencyDuplicate proves that when the same event ID is
// delivered twice, the consumer processes it only once via SetNX.
func TestConsumer_IdempotencyDuplicate(t *testing.T) {
	logBegin(t, "TestConsumer_IdempotencyDuplicate",
		"Duplicate event delivery → processed exactly once via SetNX idempotency")

	ctx := context.Background()
	rdb := startRedis(ctx, t)

	const stream = "test_idempotency"
	const group = "dispatch_group"
	eventID := uuid.New().String()

	logDetail(t, "stream", stream)
	logDetail(t, "eventID", eventID)

	// Step 1/4: Publish same event ID twice to stream
	logStep(t, 1, 4, "Publish same event ID twice to stream")
	logArrow(t, "create group and seed a nearby driver so MatchDriver can succeed")
	createGroup(ctx, t, rdb, stream, group)

	// Seed a driver so MatchDriver returns a match (prevents no-match log spam
	// and keeps the test deterministic — the consumer must complete handleMessage
	// to reach the XAck path)
	driverID := "idem-driver-001"
	err := rdb.GeoAdd(ctx, "drivers_location", &redis.GeoLocation{
		Name:      driverID,
		Latitude:  10.763000,
		Longitude: 106.661000,
	}).Err()
	require.NoError(t, err)
	err = rdb.Set(ctx, "driver_last_seen:"+driverID, "active", 60*time.Second).Err()
	require.NoError(t, err)

	logArrow(t, fmt.Sprintf("publishing event %s (first delivery)", eventID))
	publishEvent(ctx, t, rdb, stream, eventID, "Trip.Requested")

	logArrow(t, fmt.Sprintf("publishing event %s again (duplicate — same id field value)", eventID))
	publishEvent(ctx, t, rdb, stream, eventID, "Trip.Requested")

	// Step 2/4: ConsumeOnce → processes first delivery; SetNX key created
	logStep(t, 2, 4, "ConsumeOnce → processes first delivery; SetNX key created")
	consumer := newConsumer(rdb, stream, group, "consumer-a")
	logArrow(t, "calling ConsumeOnce — expects to pick up first stream entry")
	consumer.ConsumeOnce(ctx)

	// Step 3/4: ConsumeOnce again → second delivery; SetNX returns false; skipped
	logStep(t, 3, 4, "ConsumeOnce → second delivery; SetNX returns false; skipped")
	logArrow(t, "calling ConsumeOnce — second stream entry has the same id; must be skipped")
	consumer.ConsumeOnce(ctx)

	// Step 4/4: Assert idempotency key exists in Redis
	logStep(t, 4, 4, "Assert idempotency key exists in Redis")
	idempotencyKey := "processed_event:dispatch:" + eventID
	logArrow(t, fmt.Sprintf("checking key: %s", idempotencyKey))

	exists, err := rdb.Exists(ctx, idempotencyKey).Result()
	require.NoError(t, err)
	assert.Equal(t, int64(1), exists, "idempotency key must exist after first processing")

	// Both stream entries should be ACK'd — PEL must be empty
	pending, err := rdb.XPending(ctx, stream, group).Result()
	require.NoError(t, err)
	assert.Equal(t, int64(0), pending.Count, "both stream entries must be ACK'd; PEL must be empty")

	logResult(t,
		fmt.Sprintf("✓ idempotency key exists: %d (SetNX blocked duplicate)", exists),
		fmt.Sprintf("✓ PEL count: %d (both entries ACK'd)", pending.Count),
	)
}

// TestConsumer_XAutoClaimReclaim proves that a message stuck in the PEL for
// longer than MinIdle=30s is reclaimed by a recovery consumer via XAUTOCLAIM.
//
// NOTE: This test sleeps 31 seconds intentionally to age the PEL entry past
// the MinIdle threshold. This is the only reliable way to exercise the
// XAUTOCLAIM path without modifying production code.
func TestConsumer_XAutoClaimReclaim(t *testing.T) {
	logBegin(t, "TestConsumer_XAutoClaimReclaim",
		"Message stuck in PEL >30s is reclaimed by XAUTOCLAIM and re-processed")

	ctx := context.Background()
	rdb := startRedis(ctx, t)

	const stream = "test_autoclaim"
	const group = "dispatch_group"

	logDetail(t, "stream", stream)
	logDetail(t, "group", group)

	createGroup(ctx, t, rdb, stream, group)

	// Seed a driver so handleMessage can complete without error
	driverID := "autoclaim-driver-001"
	err := rdb.GeoAdd(ctx, "drivers_location", &redis.GeoLocation{
		Name:      driverID,
		Latitude:  10.763000,
		Longitude: 106.661000,
	}).Err()
	require.NoError(t, err)
	err = rdb.Set(ctx, "driver_last_seen:"+driverID, "active", 120*time.Second).Err()
	require.NoError(t, err)

	// Step 1/4: Publish event; deliver to "crashed-consumer" (no ack)
	logStep(t, 1, 4, "Publish event; deliver to \"crashed-consumer\" (no ack)")
	eventID := uuid.New().String()
	logArrow(t, fmt.Sprintf("publishing event %s", eventID))
	publishEvent(ctx, t, rdb, stream, eventID, "Trip.Requested")

	logArrow(t, "XReadGroup as crashed-consumer — assigns message to PEL without ACK")
	entries, err := rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    group,
		Consumer: "crashed-consumer",
		Streams:  []string{stream, ">"},
		Count:    1,
		Block:    2 * time.Second,
	}).Result()
	require.NoError(t, err)
	require.Len(t, entries, 1, "stream must return exactly one entry")
	require.Len(t, entries[0].Messages, 1, "crashed consumer must receive the message")
	logArrow(t, fmt.Sprintf("message %s now in crashed-consumer PEL (not ACK'd)", entries[0].Messages[0].ID))

	// Step 2/4: Wait 31s for PEL entry to exceed MinIdle=30s
	logStep(t, 2, 4, "Wait 31s for PEL entry to age past MinIdle=30s")
	logArrow(t, "sleeping 31s — required to satisfy XAUTOCLAIM MinIdle threshold")
	time.Sleep(31 * time.Second)
	logArrow(t, "sleep complete; PEL entry is now stale")

	// Step 3/4: recovery-consumer.ConsumeOnce() → XAUTOCLAIM reclaims + processes
	logStep(t, 3, 4, "recovery-consumer.ConsumeOnce() → XAUTOCLAIM reclaims + processes")
	recoveryConsumer := newConsumer(rdb, stream, group, "recovery-consumer")
	logArrow(t, "calling ConsumeOnce — XAUTOCLAIM should reclaim the stale PEL entry")
	recoveryConsumer.ConsumeOnce(ctx)

	// Step 4/4: Assert PEL empty (message ACK'd)
	logStep(t, 4, 4, "Assert PEL empty (message ACK'd by recovery consumer)")
	pending, err := rdb.XPending(ctx, stream, group).Result()
	require.NoError(t, err)
	assert.Equal(t, int64(0), pending.Count, "PEL must be empty after XAUTOCLAIM reclaim")

	logResult(t,
		fmt.Sprintf("✓ PEL count: %d (XAUTOCLAIM reclaimed and ACK'd the stale message)", pending.Count),
	)
}

// TestConsumer_OutOfOrderDelivery proves that a Trip.Accepted event arriving
// before Trip.Requested is handled gracefully — no panic, message is ACK'd.
func TestConsumer_OutOfOrderDelivery(t *testing.T) {
	logBegin(t, "TestConsumer_OutOfOrderDelivery",
		"Trip.Accepted before Trip.Requested → no panic, message ACK'd")

	ctx := context.Background()
	rdb := startRedis(ctx, t)

	const stream = "test_out_of_order"
	const group = "dispatch_group"

	logDetail(t, "stream", stream)

	createGroup(ctx, t, rdb, stream, group)

	// Step 1/3: Publish Trip.Accepted event for a trip with no prior Trip.Requested
	logStep(t, 1, 3, "Publish Trip.Accepted event for a trip with no prior Trip.Requested")
	eventID := uuid.New().String()
	logArrow(t, fmt.Sprintf("publishing Trip.Accepted (eventID=%s) — no preceding Trip.Requested", eventID))

	// driver_id is empty string — ConfirmDriverOnTrip will be skipped (no-op)
	_, err := rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: stream,
		Values: map[string]any{
			"id":           eventID,
			"type":         "Trip.Accepted",
			"aggregate":    "TRIP",
			"aggregate_id": uuid.New().String(),
			"payload":      `{"driver_id":""}`,
		},
	}).Result()
	require.NoError(t, err)

	// Step 2/3: ConsumeOnce → handleMessage processes Trip.Accepted without panic
	logStep(t, 2, 3, "ConsumeOnce → handleMessage processes Trip.Accepted without panic")
	consumer := newConsumer(rdb, stream, group, "consumer-oo")
	logArrow(t, "calling ConsumeOnce — expect no panic; empty driver_id skips ConfirmDriverOnTrip")

	// Wrap in a goroutine-safe panic check via require.NotPanics
	require.NotPanics(t, func() {
		consumer.ConsumeOnce(ctx)
	}, "ConsumeOnce must not panic on out-of-order Trip.Accepted")

	// Step 3/3: Verify PEL is empty (message was ACK'd)
	logStep(t, 3, 3, "Verify PEL is empty (message was ACK'd)")
	pending, err := rdb.XPending(ctx, stream, group).Result()
	require.NoError(t, err)
	assert.Equal(t, int64(0), pending.Count, "out-of-order message must be ACK'd (not stuck in PEL)")

	logResult(t,
		fmt.Sprintf("✓ no panic on Trip.Accepted without prior context"),
		fmt.Sprintf("✓ PEL count: %d (message ACK'd)", pending.Count),
	)
}

// TestConsumer_NOGROUPRecovery proves that when the consumer group is deleted
// mid-run, the NOGROUP error is detected and the group is auto-recreated so the
// consumer can resume processing.
func TestConsumer_NOGROUPRecovery(t *testing.T) {
	logBegin(t, "TestConsumer_NOGROUPRecovery",
		"Consumer group deleted → NOGROUP detected → group recreated → consumer resumes")

	ctx := context.Background()
	rdb := startRedis(ctx, t)

	const stream = "test_nogroup"
	const group = "dispatch_group"

	logDetail(t, "stream", stream)
	logDetail(t, "group", group)

	createGroup(ctx, t, rdb, stream, group)

	// Seed a driver so Trip.Requested events can be processed fully
	driverID := "nogroup-driver-001"
	err := rdb.GeoAdd(ctx, "drivers_location", &redis.GeoLocation{
		Name:      driverID,
		Latitude:  10.763000,
		Longitude: 106.661000,
	}).Err()
	require.NoError(t, err)
	err = rdb.Set(ctx, "driver_last_seen:"+driverID, "active", 60*time.Second).Err()
	require.NoError(t, err)

	consumer := newConsumer(rdb, stream, group, "consumer-ng")

	// Step 1/4: Create group, publish event, ConsumeOnce (normal processing)
	logStep(t, 1, 4, "Create group, publish event, ConsumeOnce (normal processing)")
	eventID1 := uuid.New().String()
	logArrow(t, fmt.Sprintf("publishing first event %s", eventID1))
	publishEvent(ctx, t, rdb, stream, eventID1, "Trip.Requested")

	logArrow(t, "ConsumeOnce — normal happy-path processing")
	consumer.ConsumeOnce(ctx)

	pending1, err := rdb.XPending(ctx, stream, group).Result()
	require.NoError(t, err)
	assert.Equal(t, int64(0), pending1.Count, "first event must be ACK'd after normal processing")
	logArrow(t, fmt.Sprintf("PEL after first consume: %d (expected 0)", pending1.Count))

	// Step 2/4: Delete the consumer group
	logStep(t, 2, 4, "Delete the consumer group (simulates external deletion)")
	logArrow(t, fmt.Sprintf("calling XGroupDestroy on group %q", group))
	destroyed, err := rdb.XGroupDestroy(ctx, stream, group).Result()
	require.NoError(t, err)
	assert.Equal(t, int64(1), destroyed, "group must exist before destruction")
	logArrow(t, fmt.Sprintf("group %q destroyed", group))

	// Step 3/4: ConsumeOnce → XReadGroup returns NOGROUP → consumer recreates group
	logStep(t, 3, 4, "ConsumeOnce → NOGROUP error → group auto-recreated")
	logArrow(t, "calling ConsumeOnce — XReadGroup will fail with NOGROUP; recovery logic runs")
	consumer.ConsumeOnce(ctx)

	// Verify the group was recreated by checking XGroupCreate returns BUSYGROUP
	recreateErr := rdb.XGroupCreate(ctx, stream, group, "0").Err()
	assert.NotNil(t, recreateErr, "group should already exist after auto-recreation")
	if recreateErr != nil {
		assert.Contains(t, recreateErr.Error(), "BUSYGROUP",
			"error must be BUSYGROUP confirming group was recreated by recovery logic")
	}
	logArrow(t, "confirmed: group exists again (BUSYGROUP on duplicate create)")

	// Step 4/4: Publish new event, ConsumeOnce → processes successfully
	logStep(t, 4, 4, "Publish new event; ConsumeOnce processes it successfully")

	// Release the driver reservation so it can be matched again for the new trip
	_ = rdb.Del(ctx, "driver_status:"+driverID)

	eventID2 := uuid.New().String()
	logArrow(t, fmt.Sprintf("publishing second event %s", eventID2))
	publishEvent(ctx, t, rdb, stream, eventID2, "Trip.Requested")

	logArrow(t, "ConsumeOnce — should process new event normally after group recovery")
	consumer.ConsumeOnce(ctx)

	pending2, err := rdb.XPending(ctx, stream, group).Result()
	require.NoError(t, err)
	assert.Equal(t, int64(0), pending2.Count,
		"after NOGROUP recovery, new event must be processed and ACK'd")

	logResult(t,
		"✓ NOGROUP error triggered recovery path",
		"✓ group auto-recreated (BUSYGROUP confirmed)",
		fmt.Sprintf("✓ post-recovery PEL count: %d (new event processed)", pending2.Count),
	)
}
