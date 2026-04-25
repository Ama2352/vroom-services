package worker

import (
	"context"
	"encoding/json"
	"log"
	"time"

	"github.com/redis/go-redis/v9"
)

type RideEventConsumer struct {
	redisClient *redis.Client
	streamName  string
	groupName   string
	consumerID  string
}

func NewRideEventConsumer(redisClient *redis.Client, streamName, groupName, consumerID string) *RideEventConsumer {
	return &RideEventConsumer{
		redisClient: redisClient,
		streamName:  streamName,
		groupName:   groupName,
		consumerID:  consumerID,
	}
}

func (c *RideEventConsumer) Start(ctx context.Context) {
	// Create consumer group if not exists
	err := c.redisClient.XGroupCreateMkStream(ctx, c.streamName, c.groupName, "0").Err()
	if err != nil && err.Error() != "BUSYGROUP Consumer Group name already exists" {
		log.Printf("Error creating consumer group: %v", err)
	}

	log.Printf("Dispatch consumer started, listening on stream: %s, group: %s", c.streamName, c.groupName)

	for {
		select {
		case <-ctx.Done():
			return
		default:
			c.consume(ctx)
		}
	}
}

func (c *RideEventConsumer) consume(ctx context.Context) {
	// Read from group
	entries, err := c.redisClient.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    c.groupName,
		Consumer: c.consumerID,
		Streams:  []string{c.streamName, ">"},
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
			c.handleMessage(ctx, message)
			// Acknowledge message
			c.redisClient.XAck(ctx, c.streamName, c.groupName, message.ID)
		}
	}
}

func (c *RideEventConsumer) handleMessage(ctx context.Context, msg redis.XMessage) {
	eventType := msg.Values["type"].(string)
	aggregateID := msg.Values["aggregate"].(string)
	payload := msg.Values["payload"].(string)

	log.Printf("Received Event: %s for Aggregate: %s", eventType, aggregateID)

	if eventType == "Trip.Requested" {
		var data map[string]interface{}
		if err := json.Unmarshal([]byte(payload), &data); err != nil {
			log.Printf("Error unmarshaling payload: %v", err)
			return
		}
		log.Printf("Processing Ride Request for Passenger: %v", data["passenger_id"])
		// TODO: Implement driver matching logic here in Phase 3
	}
}
