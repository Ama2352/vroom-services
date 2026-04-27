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
	// Robust field extraction
	getVal := func(key string) string {
		if val, ok := msg.Values[key]; ok {
			return val.(string)
		}
		return ""
	}

	eventType := getVal("type")
	aggregateType := getVal("aggregate")
	aggregateID := getVal("aggregate_id")
	if aggregateID == "" {
		aggregateID = aggregateType // Fallback to "aggregate" key if ride service changed format
	}
	payload := getVal("payload")

	log.Printf("[DEBUG] Dispatch Consumer: Received %s for %s (%s)", eventType, aggregateType, aggregateID)

	if eventType == "Trip.Requested" {
		var data map[string]interface{}
		if err := json.Unmarshal([]byte(payload), &data); err != nil {
			log.Printf("[ERROR] Dispatch Consumer: Failed to unmarshal payload for trip %s: %v", aggregateID, err)
			return
		}
		
		tripID := ""
		if id, ok := data["id"].(string); ok {
			tripID = id
		} else {
			tripID = aggregateID
		}

		lat, _ := data["source_lat"].(float64)
		lng, _ := data["source_lng"].(float64)

		log.Printf("[STEP 2] Dispatcher matching driver for Trip: %s at (%f, %f)", tripID, lat, lng)
		
		// Match Driver
		driverID, err := c.dispatchService.MatchDriver(ctx, tripID, lat, lng)
		if err != nil {
			log.Printf("[DISPATCH ERROR] MatchDriver failed for trip %s: %v", tripID, err)
			return
		}

		if driverID == "" {
			log.Printf("[STEP 2.X] MATCH FAILED: No available drivers found within radius for trip: %s", tripID)
			return
		}

		log.Printf("[STEP 2] MATCH SUCCESS: Trip %s assigned to Driver %s. Publishing 'Trip.Matched'...", tripID, driverID)
		
		// Publish Trip.Matched event
		matchPayload := map[string]interface{}{
			"id":           tripID,
			"driver_id":    driverID,
			"passenger_id": data["passenger_id"], // Preserve passenger ID for easier notification targeting
			"status":       "ACCEPTED",
			"updated_at":   time.Now().Format(time.RFC3339),
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
			log.Printf("[DISPATCH ERROR] Failed to publish Trip.Matched for trip %s: %v", tripID, err)
		} else {
			log.Printf("[STEP 2.1] Event 'Trip.Matched' published successfully for Trip: %s", tripID)
		}
	}
}
