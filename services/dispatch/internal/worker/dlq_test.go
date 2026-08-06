//go:build integration

package worker

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"

	"vroom-mvp/dispatch/internal/service"
)

func startConsumerRedis(t *testing.T) (context.Context, *redis.Client) {
	t.Helper()
	ctx := context.Background()
	if address := os.Getenv("REDIS_TEST_ADDR"); address != "" {
		client := redis.NewClient(&redis.Options{Addr: address})
		t.Cleanup(func() { require.NoError(t, client.Close()) })
		require.Eventually(t, func() bool { return client.Ping(ctx).Err() == nil },
			15*time.Second, 300*time.Millisecond, "external Redis test service not ready")
		return ctx, client
	}
	container, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			Image:        "redis:7-alpine",
			ExposedPorts: []string{"6379/tcp"},
			WaitingFor:   wait.ForLog("Ready to accept connections"),
		},
		Started: true,
	})
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, container.Terminate(ctx)) })

	host, err := container.Host(ctx)
	require.NoError(t, err)
	port, err := container.MappedPort(ctx, "6379")
	require.NoError(t, err)

	client := redis.NewClient(&redis.Options{Addr: fmt.Sprintf("%s:%s", host, port.Port())})
	t.Cleanup(func() { require.NoError(t, client.Close()) })
	require.Eventually(t, func() bool { return client.Ping(ctx).Err() == nil }, 15*time.Second, 300*time.Millisecond)
	return ctx, client
}

func publishConsumerEvent(t *testing.T, ctx context.Context, client *redis.Client, stream, eventID, eventType string) string {
	t.Helper()
	streamID, err := client.XAdd(ctx, &redis.XAddArgs{
		Stream: stream,
		Values: map[string]any{
			"id":             eventID,
			"type":           eventType,
			"aggregate":      "TRIP",
			"aggregate_id":   uuid.NewString(),
			"payload":        `{"id":"` + uuid.NewString() + `","source_lat":10.762622,"source_lng":106.660172}`,
			"correlation_id": "contract-regression",
			"traceparent":    "00-2a00e8c4fc0d54be20c53dc01a2cd39f-ebfea5c8b845ba82-01",
		},
	}).Result()
	require.NoError(t, err)
	return streamID
}

func dlqMetricValue(t *testing.T, eventType string) float64 {
	t.Helper()
	families, err := prometheus.DefaultGatherer.Gather()
	require.NoError(t, err)
	for _, family := range families {
		if family.GetName() != "vroom_dlq_events_by_type_total" {
			continue
		}
		for _, metric := range family.GetMetric() {
			for _, label := range metric.GetLabel() {
				if label.GetName() == "event_type" && label.GetValue() == eventType {
					return metric.GetCounter().GetValue()
				}
			}
		}
	}
	return 0
}

func dlqTotalMetricValue(t *testing.T) float64 {
	t.Helper()
	families, err := prometheus.DefaultGatherer.Gather()
	require.NoError(t, err)
	for _, family := range families {
		if family.GetName() != "vroom_dlq_events_total" || len(family.GetMetric()) != 1 {
			continue
		}
		return family.GetMetric()[0].GetCounter().GetValue()
	}
	t.Fatalf("vroom_dlq_events_total must be registered before the first DLQ event")
	return 0
}

func TestUnsupportedContractMovesExactEventDirectlyToDLQ(t *testing.T) {
	ctx, client := startConsumerRedis(t)
	const group = "dispatch_group"
	const eventType = "Trip.Requested.v2"
	eventID := uuid.NewString()
	stream := "test_contract_regression:" + eventID

	require.NoError(t, client.XGroupCreateMkStream(ctx, stream, group, "0").Err())
	streamID := publishConsumerEvent(t, ctx, client, stream, eventID, eventType)
	metricBefore := dlqMetricValue(t, eventType)
	totalMetricBefore := dlqTotalMetricValue(t)

	consumer := NewRideEventConsumer(client, service.NewDispatchService(client), stream, group, "consumer-v2")
	consumer.ConsumeOnce(ctx)

	dlq, err := client.XRevRangeN(ctx, dlqStreamName, "+", "-", 1).Result()
	require.NoError(t, err)
	require.Len(t, dlq, 1)
	assert.Equal(t, streamID, stringValue(dlq[0].Values["original_id"]))
	assert.Equal(t, eventType, stringValue(dlq[0].Values["event_type"]))
	assert.Equal(t, "contract-regression", stringValue(dlq[0].Values["correlation_id"]))
	assert.Equal(t, "00-2a00e8c4fc0d54be20c53dc01a2cd39f-ebfea5c8b845ba82-01", stringValue(dlq[0].Values["traceparent"]))

	pending, err := client.XPending(ctx, stream, group).Result()
	require.NoError(t, err)
	assert.Zero(t, pending.Count, "permanent failures are terminal and must be acknowledged")
	assert.Equal(t, int64(1), client.Exists(ctx, "processed_event:dispatch:"+eventID).Val(), "terminal event must be marked processed")
	assert.Equal(t, int64(0), client.Exists(ctx, "event:retry:"+streamID).Val(), "permanent failures must not consume retry budget")
	assert.Equal(t, metricBefore+1, dlqMetricValue(t, eventType))
	assert.Equal(t, totalMetricBefore+1, dlqTotalMetricValue(t), "the alerting counter must increment from its pre-existing series")
}

func TestRetryableKnownEventRemainsPendingAndUnprocessed(t *testing.T) {
	ctx, client := startConsumerRedis(t)
	const group = "dispatch_group"
	eventID := uuid.NewString()
	stream := "test_retryable_failure:" + eventID

	require.NoError(t, client.XGroupCreateMkStream(ctx, stream, group, "0").Err())
	// MatchDriver performs a GEOSEARCH on this key. A string forces a transient
	// dependency error while leaving the event contract itself valid.
	require.NoError(t, client.Del(ctx, "drivers_location").Err())
	require.NoError(t, client.Set(ctx, "drivers_location", "temporarily-invalid", 0).Err())
	t.Cleanup(func() { _ = client.Del(ctx, "drivers_location").Err() })
	streamID := publishConsumerEvent(t, ctx, client, stream, eventID, "Trip.Requested")
	dlqBefore := client.XLen(ctx, dlqStreamName).Val()

	consumer := NewRideEventConsumer(client, service.NewDispatchService(client), stream, group, "consumer-retry")
	consumer.ConsumeOnce(ctx)

	pending, err := client.XPending(ctx, stream, group).Result()
	require.NoError(t, err)
	assert.Equal(t, int64(1), pending.Count, "retryable failure must remain available for XAUTOCLAIM")
	assert.Equal(t, int64(0), client.Exists(ctx, "processed_event:dispatch:"+eventID).Val(), "retryable failure must not be marked processed")
	assert.Equal(t, "1", client.Get(ctx, "event:retry:"+streamID).Val(), "retryable failure must consume one retry attempt")
	assert.Equal(t, dlqBefore, client.XLen(ctx, dlqStreamName).Val(), "first retryable failure must not enter DLQ")
}
