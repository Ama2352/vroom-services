package testkit

import (
	"context"
	"testing"

	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/require"
)

// PublishEvent publishes a single event to a Redis stream and returns the stream entry ID.
func PublishEvent(ctx context.Context, rdb *redis.Client, stream string, values map[string]any) string {
	id, err := rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: stream,
		Values: values,
	}).Result()
	if err != nil {
		panic("testkit.PublishEvent: " + err.Error())
	}
	return id
}

// ReadGroupOnce reads up to count messages from a consumer group, non-blocking (Block: 0).
func ReadGroupOnce(ctx context.Context, rdb *redis.Client, stream, group, consumer string, count int64) []redis.XMessage {
	streams, err := rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    group,
		Consumer: consumer,
		Streams:  []string{stream, ">"},
		Count:    count,
		Block:    0,
	}).Result()
	if err != nil && err != redis.Nil {
		panic("testkit.ReadGroupOnce: " + err.Error())
	}
	if len(streams) == 0 {
		return nil
	}
	return streams[0].Messages
}

// AssertStreamHasEntry checks that the stream contains an entry with the given type value.
// Returns the matching message or fails the test.
func AssertStreamHasEntry(ctx context.Context, t *testing.T, rdb *redis.Client, stream, eventType string) redis.XMessage {
	t.Helper()

	msgs, err := rdb.XRange(ctx, stream, "-", "+").Result()
	require.NoError(t, err, "XRange on stream %q failed", stream)

	for _, msg := range msgs {
		if msg.Values["type"] == eventType {
			return msg
		}
	}

	t.Fatalf("stream %q does not contain an entry with type=%q (total entries: %d)", stream, eventType, len(msgs))
	return redis.XMessage{} // unreachable, satisfies compiler
}
