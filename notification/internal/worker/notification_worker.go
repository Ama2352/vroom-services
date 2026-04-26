package worker

import (
	"context"
	"log"
	"time"

	"github.com/redis/go-redis/v9"
)

type NotificationWorker struct {
	redisClient *redis.Client
	streamName  string
	groupName   string
	consumerID  string
}

func NewNotificationWorker(redisClient *redis.Client, streamName, groupName, consumerID string) *NotificationWorker {
	return &NotificationWorker{
		redisClient: redisClient,
		streamName:  streamName,
		groupName:   groupName,
		consumerID:  consumerID,
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
	eventType := msg.Values["type"].(string)

	switch eventType {
	case "Trip.Requested":
		log.Printf("[NOTIFICATION] Passenger: Searching for your ride... (Event: %s)", eventType)
	case "Trip.Matched":
		log.Printf("[NOTIFICATION] Passenger: Driver found! (Event: %s)", eventType)
		log.Printf("[NOTIFICATION] Driver: New trip assigned! (Event: %s)", eventType)
	case "User.Created":
		log.Printf("[NOTIFICATION] System: Welcome email sent to new user. (Event: %s)", eventType)
	default:
		log.Printf("[NOTIFICATION] Received internal event: %s", eventType)
	}
}
