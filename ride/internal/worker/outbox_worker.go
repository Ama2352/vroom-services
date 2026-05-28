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

// ProcessOnce fetches and publishes all pending outbox events in a single pass.
// Exported for use in integration tests; production code calls this via Start().
func (w *OutboxWorker) ProcessOnce(ctx context.Context) {
	w.processEvents(ctx)
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
			log.Printf("[OUTBOX ERROR] Failed to publish event %s (%s): %v. Marking as FAILED.", event.ID, event.EventType, err)
			_ = w.repo.UpdateEventStatus(ctx, event.ID, "FAILED")
			continue
		}

		err = w.repo.UpdateEventStatus(ctx, event.ID, "PUBLISHED")
		if err != nil {
			log.Printf("[OUTBOX ERROR] Failed to update event status %s to PUBLISHED: %v", event.ID, err)
		} else {
			log.Printf("[OUTBOX] Successfully published: %s for %s %s", event.EventType, event.AggregateType, event.AggregateID)
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
			"aggregate_id":  event.AggregateID.String(),
			"payload":       string(payloadBytes),
			"correlation_id": event.CorrelationID,
		},
	}).Err()


	return err
}
