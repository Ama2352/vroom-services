package worker

import (
	"context"
	"encoding/json" // Added for payload parsing
	"log"
	"time"

	"vroom-mvp/notification/internal/service"

	"database/sql"
	"github.com/redis/go-redis/v9"
)
type NotificationWorker struct {
	redisClient *redis.Client
	db          *sql.DB
	streamName  string
	groupName   string
	consumerID  string
	hub         *service.Hub
}

func NewNotificationWorker(redisClient *redis.Client, db *sql.DB, streamName, groupName, consumerID string, hub *service.Hub) *NotificationWorker {
	return &NotificationWorker{
		redisClient: redisClient,
		db:          db,
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

	if aggregateID == "" || eventType == "" {
		log.Printf("[SKIP] Skipping message %s due to missing critical fields", msg.ID)
		return
	}

	// 1. Persist to History DB (Idempotency check)
	_, err := w.db.ExecContext(ctx, 
		"INSERT INTO notification_history (event_id, event_type, aggregate_type, aggregate_id, payload) VALUES ($1, $2, $3, $4, $5)",
		msg.ID, eventType, aggregateType, aggregateID, payload)
	
	if err != nil {
		// If it's a unique constraint violation (Postgres error 23505), it's a duplicate
		// We can safely acknowledge and skip processing
		log.Printf("[DUPLICATE] Event %s already processed, skipping broadcast", msg.ID)
		return
	}

	event := map[string]interface{}{
		"id":             msg.ID,
		"event_type":     eventType,
		"aggregate_type": aggregateType,
		"aggregate_id":   aggregateID,
		"payload":        payload,
		"ts":             time.Now(),
	}

	// 2. Targeted Notifications (Attempt to extract targeted userID from payload)
	var payloadMap map[string]interface{}
	targeted := false
	if err := json.Unmarshal([]byte(payload), &payloadMap); err == nil {
		// If payload contains user_id or driver_id, send specifically to them
		if uid, ok := payloadMap["user_id"].(string); ok && uid != "" {
			log.Printf("[TARGETED] Sending %s to user %s", eventType, uid)
			w.hub.SendToUser(uid, event)
			targeted = true
		}
		if did, ok := payloadMap["driver_id"].(string); ok && did != "" {
			log.Printf("[TARGETED] Sending %s to driver %s", eventType, did)
			w.hub.SendToUser(did, event)
			targeted = true
		}
	}

	// 3. Fallback to Broadcast if not targeted or if we want global visibility for demo
	if !targeted {
		w.hub.BroadcastEvent(event)
	}

	// Logging for visibility
	switch eventType {
	case "Trip.Requested":
		log.Printf("[NOTIFICATION] Passenger: Searching for your ride... (Event: %s)", eventType)
	case "Trip.Matched":
		log.Printf("[NOTIFICATION] Trip Matched! (Event: %s)", eventType)
	case "User.Created":
		log.Printf("[NOTIFICATION] System: Welcome email sent to new user. (Event: %s)", eventType)
	default:
		log.Printf("[NOTIFICATION] Received event: %s", eventType)
	}
}
