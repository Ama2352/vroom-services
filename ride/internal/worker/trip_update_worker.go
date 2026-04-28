package worker

import (
	"context"
	"encoding/json"
	"log"
	"time"
	"vroom-mvp/ride/internal/repository"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

type TripUpdateWorker struct {
	redisClient *redis.Client
	repo        repository.TripRepository
	streamName  string
	groupName   string
	consumerID  string
}

func NewTripUpdateWorker(redisClient *redis.Client, repo repository.TripRepository, streamName, groupName, consumerID string) *TripUpdateWorker {
	return &TripUpdateWorker{
		redisClient: redisClient,
		repo:        repo,
		streamName:  streamName,
		groupName:   groupName,
		consumerID:  consumerID,
	}
}

func (w *TripUpdateWorker) Start(ctx context.Context) {
	// Create consumer group
	err := w.redisClient.XGroupCreateMkStream(ctx, w.streamName, w.groupName, "0").Err()
	if err != nil && err.Error() != "BUSYGROUP Consumer Group name already exists" {
		log.Printf("Error creating consumer group for ride update: %v", err)
	}

	log.Printf("Trip update worker started, listening on stream: %s", w.streamName)

	for {
		select {
		case <-ctx.Done():
			return
		default:
			w.consume(ctx)
		}
	}
}

func (w *TripUpdateWorker) consume(ctx context.Context) {
	entries, err := w.redisClient.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    w.groupName,
		Consumer: w.consumerID,
		Streams:  []string{w.streamName, ">"},
		Count:    1,
		Block:    5 * time.Second,
	}).Result()

	if err != nil {
		if err != redis.Nil {
			log.Printf("Error reading from stream (ride update): %v", err)
		}
		return
	}

	for _, stream := range entries {
		for _, message := range stream.Messages {
			// Idempotency check
			msgID, err := uuid.Parse(message.Values["id"].(string))
			if err == nil {
				processed, _ := w.repo.IsEventProcessed(ctx, msgID)
				if processed {
					log.Printf("[IDEMPOTENCY] Event %s already processed, skipping", msgID)
					w.redisClient.XAck(ctx, w.streamName, w.groupName, message.ID)
					continue
				}
				
				w.handleMessage(ctx, message)
				
				// Mark as processed
				_ = w.repo.MarkEventProcessed(ctx, msgID, message.Values["type"].(string))
			} else {
				w.handleMessage(ctx, message)
			}
			
			w.redisClient.XAck(ctx, w.streamName, w.groupName, message.ID)
		}
	}
}


func (w *TripUpdateWorker) handleMessage(ctx context.Context, msg redis.XMessage) {
	eventType := msg.Values["type"].(string)
	payload := msg.Values["payload"].(string)

	if eventType == "Trip.Matched" {
		var data struct {
			ID       uuid.UUID `json:"id"`
			DriverID uuid.UUID `json:"driver_id"`
			Status   string    `json:"status"`
		}

		if err := json.Unmarshal([]byte(payload), &data); err != nil {
			log.Printf("Error unmarshaling match payload: %v", err)
			return
		}

		log.Printf("[STEP 3] Ride service received 'Trip.Matched' for Trip: %s. Assigning Driver: %s...", data.ID, data.DriverID)
		
		// 1. Prepare Outbox Event
		outboxEvent := &repository.OutboxEvent{
			ID:            uuid.New(),
			AggregateType: "TRIP",
			AggregateID:   data.ID,
			EventType:     "Trip.Accepted",
			Payload: map[string]interface{}{
				"id":         data.ID,
				"driver_id":  data.DriverID,
				"status":     "ACCEPTED",
				"updated_at": time.Now(),
			},
		}

		// 2. Update trip with driver info and status atomically with outbox
		err := w.repo.AcceptWithOutbox(ctx, data.ID, data.DriverID, outboxEvent)
		if err != nil {
			log.Printf("[RIDE ERROR] Error accepting trip in DB: %v", err)
		} else {
			log.Printf("[STEP 3.1] Trip %s status updated to ACCEPTED in Database and Outbox.", data.ID)
		}
	} else if eventType == "Trip.MatchFailed" {
		var data struct {
			ID     uuid.UUID `json:"id"`
			Reason string    `json:"reason"`
		}

		if err := json.Unmarshal([]byte(payload), &data); err != nil {
			log.Printf("Error unmarshaling MatchFailed payload: %v", err)
			return
		}

		log.Printf("[SAGA] Ride service received 'Trip.MatchFailed' for Trip: %s. Reason: %s. Cancelling trip...", data.ID, data.Reason)

		// Update trip status to CANCELLED
		err := w.repo.UpdateStatus(ctx, data.ID, "CANCELLED")
		if err != nil {
			log.Printf("[RIDE ERROR] Error cancelling trip in DB: %v", err)
		} else {
			log.Printf("[SAGA] Trip %s status updated to CANCELLED in Database.", data.ID)
		}
	}
}

