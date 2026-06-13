package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"time"

	"vroom-mvp/notification/internal/repository"
	"vroom-mvp/notification/internal/service"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
)

const (
	notifMaxEventRetries = 3
	notifDLQStreamName   = "ride_events_dlq"
)

var notifDLQEventsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
	Name: "vroom_notification_dlq_events_total",
	Help: "Events sent to DLQ by notification consumer after exhausting retries",
}, []string{"event_type"})

type NotificationWorker struct {
	redisClient *redis.Client
	repo        repository.NotificationRepository
	streamName  string
	groupName   string
	consumerID  string
	hub         *service.Hub
}

func NewNotificationWorker(redisClient *redis.Client, repo repository.NotificationRepository, streamName, groupName, consumerID string, hub *service.Hub) *NotificationWorker {
	return &NotificationWorker{
		redisClient: redisClient,
		repo:        repo,
		streamName:  streamName,
		groupName:   groupName,
		consumerID:  consumerID,
		hub:         hub,
	}
}

func (w *NotificationWorker) Start(ctx context.Context) {
	// Create consumer group
	err := w.redisClient.XGroupCreateMkStream(ctx, w.streamName, w.groupName, "0").Err()
	if err != nil && err.Error() != "BUSYGROUP Consumer Group name already exists" {
		log.Printf("Error creating consumer group: %v", err)
	}

	log.Printf("Notification worker started, listening on stream: %s, group: %s", w.streamName, w.groupName)

	for {
		select {
		case <-ctx.Done():
			return
		default:
			w.consume(ctx)
		}
	}
}

func (w *NotificationWorker) consume(ctx context.Context) {
	// Reclaim messages stuck in PEL > 30s (crash recovery)
	autoclaimed, _, _ := w.redisClient.XAutoClaim(ctx, &redis.XAutoClaimArgs{
		Stream:   w.streamName,
		Group:    w.groupName,
		Consumer: w.consumerID,
		MinIdle:  30 * time.Second,
		Start:    "0-0",
		Count:    10,
	}).Result()
	for _, msg := range autoclaimed {
		w.processWithDLQ(ctx, msg)
		w.redisClient.XAck(ctx, w.streamName, w.groupName, msg.ID)
	}

	entries, err := w.redisClient.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    w.groupName,
		Consumer: w.consumerID,
		Streams:  []string{w.streamName, ">"},
		Count:    10,
		Block:    5 * time.Second,
	}).Result()
	if err != nil {
		if err != redis.Nil {
			log.Printf("Error reading from Redis Stream: %v", err)
			if strings.Contains(err.Error(), "NOGROUP") {
				log.Printf("[RECOVERY] Consumer group 'notification_group' missing. Recreating...")
				_ = w.redisClient.XGroupCreateMkStream(ctx, w.streamName, w.groupName, "0").Err()
			}
		}
		return
	}
	for _, stream := range entries {
		for _, message := range stream.Messages {
			w.processWithDLQ(ctx, message)
			w.redisClient.XAck(ctx, w.streamName, w.groupName, message.ID)
		}
	}
}

func (w *NotificationWorker) processWithDLQ(ctx context.Context, msg redis.XMessage) {
	retryKey := "event:notif:retry:" + msg.ID
	retries, _ := w.redisClient.Incr(ctx, retryKey).Result()
	w.redisClient.Expire(ctx, retryKey, 24*time.Hour)

	w.handleMessage(ctx, msg)

	eventType := ""
	if v, ok := msg.Values["type"]; ok && v != nil {
		eventType = v.(string)
	}
	aggregateID := ""
	if v, ok := msg.Values["aggregate_id"]; ok && v != nil {
		aggregateID = v.(string)
	}

	isMalformed := eventType == "" || aggregateID == ""
	if isMalformed && retries >= notifMaxEventRetries {
		payload := ""
		if v, ok := msg.Values["payload"]; ok {
			payload = v.(string)
		}
		_ = w.redisClient.XAdd(ctx, &redis.XAddArgs{
			Stream: notifDLQStreamName,
			Values: map[string]any{
				"original_id": msg.ID,
				"event_type":  eventType,
				"error":       fmt.Sprintf("malformed event after %d retries", retries),
				"payload":     payload,
				"failed_at":   time.Now().Format(time.RFC3339),
			},
		}).Err()
		_ = w.redisClient.Del(ctx, retryKey).Err()
		notifDLQEventsTotal.WithLabelValues(eventType).Inc()
		log.Printf("[DLQ] Notification event %s moved to DLQ after %d retries", msg.ID, retries)
	}
}

func (w *NotificationWorker) handleMessage(ctx context.Context, msg redis.XMessage) {
	tracer := otel.Tracer("notification-consumer")
	carrier := propagation.MapCarrier{}
	if tp, ok := msg.Values["traceparent"]; ok && tp != nil {
		carrier["traceparent"] = tp.(string)
	}
	if ts, ok := msg.Values["tracestate"]; ok && ts != nil {
		carrier["tracestate"] = ts.(string)
	}
	remoteCtx := otel.GetTextMapPropagator().Extract(context.Background(), carrier)
	remoteSpan := trace.SpanContextFromContext(remoteCtx)

	eventType := ""
	if v, ok := msg.Values["type"]; ok && v != nil {
		eventType = v.(string)
	}
	ctx, span := tracer.Start(ctx, "notification.consume."+eventType,
		trace.WithLinks(trace.Link{SpanContext: remoteSpan}),
	)
	defer span.End()

	// Existing getVal helper follows:
	getVal := func(key string) string {
		if val, ok := msg.Values[key]; ok && val != nil {
			return val.(string)
		}
		return ""
	}

	eventType = getVal("type")
	aggregateType := getVal("aggregate")
	aggregateID := getVal("aggregate_id")
	payload := getVal("payload")

	log.Printf("[DEBUG] NotificationWorker handling event: %s for aggregate: %s", eventType, aggregateID)

	if aggregateID == "" || eventType == "" {
		log.Printf("[SKIP] Skipping message %s due to missing critical fields (Type: %s, ID: %s)", msg.ID, eventType, aggregateID)
		return
	}

	// Persist to history DB; unique constraint on event_id acts as idempotency guard.
	if err := w.repo.SaveEvent(ctx, msg.ID, eventType, aggregateType, aggregateID, payload); err != nil {
		if strings.Contains(err.Error(), "duplicate key value violates unique constraint") {
			log.Printf("[DUPLICATE] Event %s already processed, skipping", msg.ID)
			return
		}
		// DB failure is non-fatal — don't block the real-time WebSocket notification.
		log.Printf("[DB ERROR] Failed to persist notification history: %v", err)
	}

	event := map[string]interface{}{
		"id":             msg.ID,
		"event_type":     eventType,
		"aggregate_type": aggregateType,
		"aggregate_id":   aggregateID,
		"payload":        payload,
		"ts":             time.Now(),
	}

	// 2. Targeted Notifications (Attempt to extract targeted ID from payload)
	var payloadMap map[string]interface{}
	targeted := false
	if err := json.Unmarshal([]byte(payload), &payloadMap); err == nil {
		// Try various ID keys used in the system (user_id, driver_id, passenger_id)
		idKeys := []string{"user_id", "driver_id", "passenger_id"}
		for _, key := range idKeys {
			if id, ok := payloadMap[key].(string); ok && id != "" {
				log.Printf("[TARGETED] Sending %s to %s %s", eventType, key, id)
				w.hub.SendToUser(id, event)
				targeted = true
			}
		}
	}

	// 3. Fallback to Broadcast if not targeted or for core demo events
	// We always broadcast core lifecycle events so the multi-view demo dashboard stays in sync
	isCoreEvent := eventType == "Trip.Requested" || eventType == "Trip.Matched" || 
		eventType == "Trip.MatchFailed" ||
		eventType == "Trip.Started" || eventType == "Trip.Completed" || 
		eventType == "Trip.Cancelled" || 
		eventType == "Trip.OfferRejected"


	if !targeted || isCoreEvent {
		log.Printf("[BROADCAST] Event %s (ID: %s) to all clients", eventType, msg.ID)
		w.hub.BroadcastEvent(event)
	}

	// Logging for visibility
	switch eventType {
	case "Trip.Requested":
		log.Printf("[NOTIFICATION] Passenger: Searching for your ride... (Event: %s)", eventType)
	case "Trip.Matched":
		log.Printf("[NOTIFICATION] Trip Matched! (Event: %s)", eventType)
	case "Trip.MatchFailed":
		log.Printf("[NOTIFICATION] Passenger: Sorry, no drivers available. (Event: %s)", eventType)
	case "User.Created":

		log.Printf("[NOTIFICATION] System: Welcome email sent to new user. (Event: %s)", eventType)
	default:
		log.Printf("[NOTIFICATION] Received event: %s", eventType)
	}
}
