package worker

import (
	"context"
	"encoding/json"
	"log"
	"strings"
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
		Count:    10,
		Block:    5 * time.Second,
	}).Result()

	if err != nil {
		if err != redis.Nil {
			log.Printf("Error reading from stream (ride update): %v", err)
			// If group doesn't exist, try to recreate it
			if strings.Contains(err.Error(), "NOGROUP") {
				log.Printf("[RECOVERY] Consumer group 'ride_update_group' missing (NOGROUP). Attempting to recreate...")
				if err := w.redisClient.XGroupCreateMkStream(ctx, w.streamName, w.groupName, "0").Err(); err != nil {
					log.Printf("[RECOVERY ERROR] Failed to recreate group: %v", err)
				} else {
					log.Printf("[RECOVERY SUCCESS] Consumer group 'ride_update_group' recreated.")
				}
			}
		}
		return
	}

	for _, stream := range entries {
		for _, message := range stream.Messages {
			// Idempotency check
			var eventID string
			if val, ok := message.Values["id"]; ok && val != nil {
				eventID = val.(string)
			} else {
				eventID = message.ID // Fallback to Redis Stream ID
			}

			msgID, err := uuid.Parse(eventID)
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
		
		// 1. Just update the driver assignment but keep status as REQUESTED/MATCHED
		// This allows the manual Accept call to proceed as expected by the user.
		err := w.repo.UpdateDriver(ctx, data.ID, data.DriverID)
		if err != nil {
			log.Printf("[RIDE ERROR] Error assigning driver in DB: %v", err)
		} else {
			log.Printf("[STEP 3] Driver %s assigned to Trip %s. Waiting for manual acceptance.", data.DriverID, data.ID)
			// Saga: record offer deadline so TripTimeoutWorker can trigger per-offer expiry
			deadline := time.Now().Add(10 * time.Second)
			if err := w.repo.SetOfferDeadline(ctx, data.ID, deadline); err != nil {
				log.Printf("[SAGA] Failed to set offer_deadline for Trip %s: %v", data.ID, err)
			}
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

// ConsumeOnce runs a single consume cycle — used by integration tests for deterministic control.
func (w *TripUpdateWorker) ConsumeOnce(ctx context.Context) {
	w.consume(ctx)
}

