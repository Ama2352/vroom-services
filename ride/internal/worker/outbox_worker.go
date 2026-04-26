package worker

import (
	"context"
	"encoding/json"
	"log"
	"time"
	"vroom-mvp/ride/internal/repository"

	"github.com/redis/go-redis/v9"
)

type OutboxWorker struct {
	repo        repository.TripRepository
	redisClient *redis.Client
	streamName  string
}

func NewOutboxWorker(repo repository.TripRepository, redisClient *redis.Client, streamName string) *OutboxWorker {
	return &OutboxWorker{
		repo:        repo,
		redisClient: redisClient,
		streamName:  streamName,
	}
}

func (w *OutboxWorker) Start(ctx context.Context) {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()

	log.Printf("Outbox worker started, polling every 2s for stream: %s", w.streamName)

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			w.processEvents(ctx)
		}
	}
}

func (w *OutboxWorker) processEvents(ctx context.Context) {
	events, err := w.repo.GetUnpublishedEvents(ctx, 10)
	if err != nil {
		log.Printf("Error fetching outbox events: %v", err)
		return
	}

	for _, event := range events {
		err := w.publishToRedis(ctx, event)
		if err != nil {
			log.Printf("Error publishing event %s to Redis: %v", event.ID, err)
			continue
		}

		err = w.repo.UpdateEventStatus(ctx, event.ID, "PUBLISHED")
		if err != nil {
			log.Printf("Error updating event status %s: %v", event.ID, err)
		} else {
			log.Printf("Successfully published event: %s (%s)", event.EventType, event.AggregateID)
		}
	}
}

func (w *OutboxWorker) publishToRedis(ctx context.Context, event *repository.OutboxEvent) error {
	payloadBytes, err := json.Marshal(event.Payload)
	if err != nil {
		return err
	}

	// Publish to Redis Stream
	err = w.redisClient.XAdd(ctx, &redis.XAddArgs{
		Stream: w.streamName,
		Values: map[string]interface{}{
			"id":           event.ID.String(),
			"type":         event.EventType,
			"aggregate":    event.AggregateType,
			"aggregate_id": event.AggregateID.String(),
			"payload":      string(payloadBytes),
		},
	}).Err()

	return err
}
