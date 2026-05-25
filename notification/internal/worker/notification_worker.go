package worker

import (
	"context"
	"encoding/json"
	"log"
	"strings"
	"time"

	"vroom-mvp/notification/internal/repository"
	"vroom-mvp/notification/internal/service"

	"github.com/redis/go-redis/v9"
)

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
	entries, err := w.redisClient.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    w.groupName,
		Consumer: w.consumerID,
		Streams:  []string{w.streamName, ">"},
		Count:    1,
		Block:    5 * time.Second,
	}).Result()

	if err != nil {
		if err != redis.Nil {
			log.Printf("Error reading from Redis Stream: %v", err)
			// If group doesn't exist, try to recreate it
			if strings.Contains(err.Error(), "NOGROUP") {
				log.Printf("[RECOVERY] Consumer group 'notification_group' missing (NOGROUP). Attempting to recreate...")
				if err := w.redisClient.XGroupCreateMkStream(ctx, w.streamName, w.groupName, "0").Err(); err != nil {
					log.Printf("[RECOVERY ERROR] Failed to recreate group: %v", err)
				} else {
					log.Printf("[RECOVERY SUCCESS] Consumer group 'notification_group' recreated.")
				}
			}
		}
		return
	}

	for _, stream := range entries {
		for _, message := range stream.Messages {
			w.handleMessage(ctx, message)
			w.redisClient.XAck(ctx, w.streamName, w.groupName, message.ID)
		}
	}
}

func (w *NotificationWorker) handleMessage(ctx context.Context, msg redis.XMessage) {
	getVal := func(key string) string {
		if val, ok := msg.Values[key]; ok && val != nil {
			return val.(string)
		}
		return ""
	}

	eventType := getVal("type")
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
