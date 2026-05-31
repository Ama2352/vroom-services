//go:build integration

package integration_test

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestRedisStreamsConsumerGroups verifies that dispatch_group and notification_group
// each receive every event independently — consuming from one group does not drain the other.
func TestRedisStreamsConsumerGroups(t *testing.T) {
	ctx := context.Background()
	rdb := startRedisContainer(ctx, t)

	const streamName = "test_ride_events_streams"

	// Create both consumer groups before publishing
	for _, group := range []string{"dispatch_group", "notification_group"} {
		err := rdb.XGroupCreateMkStream(ctx, streamName, group, "0").Err()
		require.NoError(t, err, "failed to create group %s", group)
	}

	// Publish two events
	for range 2 {
		tripID := uuid.New().String()
		_, err := rdb.XAdd(ctx, &redis.XAddArgs{
			Stream: streamName,
			Values: map[string]interface{}{
				"id":           uuid.New().String(),
				"type":         "Trip.Requested",
				"aggregate":    "TRIP",
				"aggregate_id": tripID,
				"payload":      `{"id":"` + tripID + `"}`,
			},
		}).Result()
		require.NoError(t, err)
	}

	// --- dispatch_group reads both events ---
	dispatchMsgs, err := rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    "dispatch_group",
		Consumer: "dispatch-test-1",
		Streams:  []string{streamName, ">"},
		Count:    10,
		Block:    2 * time.Second,
	}).Result()
	require.NoError(t, err)
	require.Len(t, dispatchMsgs, 1)
	assert.Len(t, dispatchMsgs[0].Messages, 2, "dispatch_group should see both events")

	// Ack both in dispatch_group
	for _, msg := range dispatchMsgs[0].Messages {
		rdb.XAck(ctx, streamName, "dispatch_group", msg.ID)
	}

	// --- notification_group must still see the same events independently ---
	notifMsgs, err := rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    "notification_group",
		Consumer: "notif-test-1",
		Streams:  []string{streamName, ">"},
		Count:    10,
		Block:    2 * time.Second,
	}).Result()
	require.NoError(t, err)
	require.Len(t, notifMsgs, 1)
	assert.Len(t, notifMsgs[0].Messages, 2, "notification_group should see both events regardless of dispatch ack")

	// Verify we got the same message IDs
	dispatchIDs := make([]string, 0, 2)
	for _, m := range dispatchMsgs[0].Messages {
		dispatchIDs = append(dispatchIDs, m.ID)
	}
	notifIDs := make([]string, 0, 2)
	for _, m := range notifMsgs[0].Messages {
		notifIDs = append(notifIDs, m.ID)
	}
	assert.ElementsMatch(t, dispatchIDs, notifIDs, "both groups must receive the same stream entries")
}
