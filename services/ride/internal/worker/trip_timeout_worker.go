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

// CheckTimeoutsOnce runs one timeout check cycle — used by integration tests for deterministic control.
func (w *TripTimeoutWorker) CheckTimeoutsOnce(ctx context.Context) {
	w.checkTimeouts(ctx)
}

func (w *TripTimeoutWorker) checkTimeouts(ctx context.Context) {
	now := time.Now()

	// Check 1: dead trips — REQUESTED with no driver for too long
	cutoff := now.Add(-time.Duration(w.timeoutSec) * time.Second)
	w.cancelStuckRequested(ctx, cutoff)

	// Check 2: per-offer timeout — REQUESTED with driver but offer_deadline has passed
	w.rejectExpiredOffers(ctx, now)

	// Check 3: ACCEPTED but driver never started the trip in 5 minutes
	acceptedCutoff := now.Add(-5 * time.Minute)
	w.cancelStuckAccepted(ctx, acceptedCutoff)
}

func (w *TripTimeoutWorker) cancelStuckRequested(ctx context.Context, cutoff time.Time) {
	trips, err := w.repo.GetStuckTrips(ctx, cutoff)
	if err != nil {
		log.Printf("[TIMEOUT WORKER] Error fetching stuck trips: %v", err)
		return
	}

	for _, trip := range trips {
		log.Printf("[TIMEOUT WORKER] Trip %s has timed out. Transitioning to CANCELLED...", trip.ID)

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

		if err := w.repo.CancelWithOutbox(ctx, trip.ID, event); err != nil {
			log.Printf("[TIMEOUT WORKER] Error cancelling trip %s: %v", trip.ID, err)
		} else {
			log.Printf("[TIMEOUT WORKER] Trip %s cancelled via Outbox (REQUEST_TIMEOUT).", trip.ID)
		}
	}
}

func (w *TripTimeoutWorker) rejectExpiredOffers(ctx context.Context, now time.Time) {
	trips, err := w.repo.GetExpiredOffers(ctx, now)
	if err != nil {
		log.Printf("[TIMEOUT WORKER] Error fetching expired offers: %v", err)
		return
	}

	for _, trip := range trips {
		driverIDStr := ""
		if trip.DriverID != nil {
			driverIDStr = trip.DriverID.String()
		}
		log.Printf("[SAGA] Offer for Trip %s expired (driver %s). Triggering compensation...", trip.ID, driverIDStr)

		event := &repository.OutboxEvent{
			ID:            uuid.New(),
			AggregateType: "TRIP",
			AggregateID:   trip.ID,
			EventType:     "Trip.OfferRejected",
			Payload: map[string]interface{}{
				"id":         trip.ID,
				"driver_id":  driverIDStr,
				"source_lat": trip.Source.Point.Lat,
				"source_lng": trip.Source.Point.Lng,
				"reason":     "OFFER_TIMEOUT",
				"updated_at": time.Now(),
			},
		}

		if err := w.repo.RejectOfferWithOutbox(ctx, trip.ID, event); err != nil {
			log.Printf("[SAGA] Error rejecting expired offer for trip %s: %v", trip.ID, err)
		} else {
			log.Printf("[SAGA] Trip %s offer rejected via Outbox (OFFER_TIMEOUT).", trip.ID)
		}
	}
}

func (w *TripTimeoutWorker) cancelStuckAccepted(ctx context.Context, cutoff time.Time) {
	trips, err := w.repo.GetStuckAcceptedTrips(ctx, cutoff)
	if err != nil {
		log.Printf("[TIMEOUT WORKER] Error fetching stuck accepted trips: %v", err)
		return
	}

	for _, trip := range trips {
		driverIDStr := ""
		if trip.DriverID != nil {
			driverIDStr = trip.DriverID.String()
		}
		log.Printf("[SAGA] Trip %s accepted but not started in 5 min. Cancelling (START_TIMEOUT)...", trip.ID)

		event := &repository.OutboxEvent{
			ID:            uuid.New(),
			AggregateType: "TRIP",
			AggregateID:   trip.ID,
			EventType:     "Trip.Cancelled",
			Payload: map[string]interface{}{
				"id":        trip.ID,
				"driver_id": driverIDStr,
				"reason":    "START_TIMEOUT",
				"updated_at": time.Now(),
			},
		}

		if err := w.repo.CancelWithOutbox(ctx, trip.ID, event); err != nil {
			log.Printf("[SAGA] Error cancelling stuck accepted trip %s: %v", trip.ID, err)
		} else {
			log.Printf("[SAGA] Trip %s cancelled via Outbox (START_TIMEOUT).", trip.ID)
		}
	}
}
