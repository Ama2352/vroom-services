package worker

import (
	"context"
	"log"
	"time"
	"vroom-mvp/ride/internal/repository"

	"github.com/google/uuid"
)

type TripTimeoutWorker struct {
	repo       repository.TripRepository
	interval   time.Duration
	timeoutSec int
}

func NewTripTimeoutWorker(repo repository.TripRepository, interval time.Duration, timeoutSec int) *TripTimeoutWorker {
	return &TripTimeoutWorker{
		repo:       repo,
		interval:   interval,
		timeoutSec: timeoutSec,
	}
}

func (w *TripTimeoutWorker) Start(ctx context.Context) {
	ticker := time.NewTicker(w.interval)
	defer ticker.Stop()

	log.Printf("Trip timeout detector started (Interval: %v, Timeout: %ds)", w.interval, w.timeoutSec)

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			w.checkTimeouts(ctx)
		}
	}
}

func (w *TripTimeoutWorker) checkTimeouts(ctx context.Context) {
	cutoff := time.Now().Add(-time.Duration(w.timeoutSec) * time.Second)
	trips, err := w.repo.GetStuckTrips(ctx, cutoff)
	if err != nil {
		log.Printf("[TIMEOUT WORKER] Error fetching stuck trips: %v", err)
		return
	}

	for _, trip := range trips {
		log.Printf("[TIMEOUT WORKER] Trip %s has timed out. Transitioning to CANCELLED...", trip.ID)
		
		// Create Outbox Event
		event := &repository.OutboxEvent{
			ID:            uuid.New(),
			AggregateType: "TRIP",
			AggregateID:   trip.ID,
			EventType:     "Trip.MatchFailed",
			Payload: map[string]interface{}{
				"id":         trip.ID,
				"reason":     "REQUEST_TIMEOUT",
				"updated_at": time.Now(),
			},
		}

		// Update status and create outbox event atomically
		err = w.repo.CancelWithOutbox(ctx, trip.ID, event)
		if err != nil {
			log.Printf("[TIMEOUT WORKER] Error cancelling trip %s: %v", trip.ID, err)
		} else {
			log.Printf("[TIMEOUT WORKER] Trip %s successfully cancelled via Outbox.", trip.ID)
		}
	}
}
