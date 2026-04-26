package worker

import (
	"context"
	"encoding/json"
	"log"
	"time"
	"vroom-mvp/dispatch/internal/service"

	"github.com/redis/go-redis/v9"
)

type RideEventConsumer struct {
	redisClient     *redis.Client
	dispatchService *service.DispatchService
	streamName      string
	groupName       string
	consumerID      string
}

func NewRideEventConsumer(redisClient *redis.Client, dispatchService *service.DispatchService, streamName, groupName, consumerID string) *RideEventConsumer {
	return &RideEventConsumer{
		redisClient:     redisClient,
		dispatchService: dispatchService,
		streamName:      streamName,
		groupName:       groupName,
		consumerID:      consumerID,
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
		
		tripID := data["id"].(string)
		lat := data["source_lat"].(float64)
		lng := data["source_lng"].(float64)

		log.Printf("[STEP 2] Dispatcher received 'Trip.Requested'. Matching driver for Trip: %s", tripID)
		
		// Match Driver
		driverID, err := c.dispatchService.MatchDriver(ctx, tripID, lat, lng)
		if err != nil {
			log.Printf("[DISPATCH ERROR] Error matching driver: %v", err)
			return
		}

		if driverID == "" {
			log.Printf("[STEP 2.X] Match Failed: No available drivers found for trip: %s", tripID)
			return
		}

		log.Printf("[STEP 2] MATCH SUCCESS: Trip %s assigned to Driver %s. Publishing match event...", tripID, driverID)
		
		// Publish Trip.Matched event
		matchPayload := map[string]interface{}{
			"id":         tripID,
			"driver_id":  driverID,
			"status":     "ACCEPTED",
			"updated_at": time.Now().Format(time.RFC3339),
		}
		payloadJSON, _ := json.Marshal(matchPayload)

		err = c.redisClient.XAdd(ctx, &redis.XAddArgs{
			Stream: c.streamName,
			Values: map[string]interface{}{
				"type":         "Trip.Matched",
				"aggregate":    "TRIP",
				"aggregate_id": tripID,
				"payload":      string(payloadJSON),
			},
		}).Err()

		if err != nil {
			log.Printf("[DISPATCH ERROR] Error publishing Trip.Matched event: %v", err)
		} else {
			log.Printf("[STEP 2.1] Event 'Trip.Matched' published to stream for Trip: %s", tripID)
		}
	}
}
