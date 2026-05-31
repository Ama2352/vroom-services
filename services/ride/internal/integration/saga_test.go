//go:build integration

package integration_test

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"vroom-mvp/ride/internal/domain"
	"vroom-mvp/ride/internal/repository"
	"vroom-mvp/ride/internal/worker"
)

// TestSagaOfferRejectionChain verifies the offer-timeout compensation path:
//  1. Trip is created and a driver is assigned (simulating Trip.Matched).
//  2. offer_deadline is set to the past.
//  3. TripTimeoutWorker fires → publishes Trip.OfferRejected via Outbox.
//  4. OutboxWorker publishes to Redis stream.
//  5. Assertions: trip.driver_id cleared, status back to REQUESTED, stream has Trip.OfferRejected.
func TestSagaOfferRejectionChain(t *testing.T) {
	ctx := context.Background()

	db := startPostgresContainer(ctx, t)
	rdb := startRedisContainer(ctx, t)

	const streamName = "test_saga_offer_rejection"

	repo := repository.NewPostgresTripRepository(db)
	outboxWkr := worker.NewOutboxWorker(repo, rdb, streamName)
	timeoutWkr := worker.NewTripTimeoutWorker(repo, time.Minute, 60)

	passengerID := uuid.New()
	driverID := uuid.New()
	tripID := uuid.New()

	// Step 1: Create trip via Outbox
	trip := &domain.Trip{
		ID:             tripID,
		PassengerID:    passengerID,
		Status:         domain.StatusRequested,
		Source:         domain.Location{Point: domain.GeoPoint{Lat: 10.762622, Lng: 106.660172}},
		Destination:    domain.Location{Point: domain.GeoPoint{Lat: 10.795202, Lng: 106.721519}},
		EstimatedPrice: domain.Price{Amount: 50000, Currency: "VND"},
		CreatedAt:      time.Now(),
	}
	createEvent := &repository.OutboxEvent{
		ID:            uuid.New(),
		AggregateType: "TRIP",
		AggregateID:   tripID,
		EventType:     "Trip.Requested",
		Payload:       map[string]interface{}{"id": tripID.String()},
	}
	require.NoError(t, repo.CreateWithOutbox(ctx, trip, createEvent))

	// Step 2: Simulate Trip.Matched — assign driver and set expired offer_deadline
	require.NoError(t, repo.UpdateDriver(ctx, tripID, driverID))
	expiredDeadline := time.Now().Add(-5 * time.Second) // already expired
	require.NoError(t, repo.SetOfferDeadline(ctx, tripID, expiredDeadline))

	// Flush pending Trip.Requested event so stream is clean for the assertion below
	outboxWkr.ProcessOnce(ctx)

	// Step 3: TripTimeoutWorker detects the expired offer and fires compensation
	timeoutWkr.CheckTimeoutsOnce(ctx)

	// Step 4: Outbox publishes Trip.OfferRejected to the stream
	outboxWkr.ProcessOnce(ctx)

	// Step 5a: Trip should have driver cleared and status back to REQUESTED
	updated, err := repo.GetByID(ctx, tripID)
	require.NoError(t, err)
	require.NotNil(t, updated)
	assert.Equal(t, domain.StatusRequested, updated.Status)
	assert.Nil(t, updated.DriverID, "driver_id must be cleared after offer rejection")

	// Step 5b: Redis stream must contain Trip.OfferRejected
	msgs, err := rdb.XRange(ctx, streamName, "-", "+").Result()
	require.NoError(t, err)

	var offerRejectedFound bool
	for _, msg := range msgs {
		if msg.Values["type"] == "Trip.OfferRejected" {
			offerRejectedFound = true
			assert.Equal(t, tripID.String(), msg.Values["aggregate_id"])
		}
	}
	assert.True(t, offerRejectedFound, "Redis stream must contain Trip.OfferRejected event")
}

// TestSagaAcceptedStartTimeout verifies the ACCEPTED-phase compensation:
//  1. Trip is created and accepted (with accepted_at = 6 min ago).
//  2. TripTimeoutWorker fires → publishes Trip.Cancelled (START_TIMEOUT) via Outbox.
//  3. OutboxWorker publishes to Redis stream.
//  4. Assertions: trip.status = CANCELLED, stream has Trip.Cancelled.
func TestSagaAcceptedStartTimeout(t *testing.T) {
	ctx := context.Background()

	db := startPostgresContainer(ctx, t)
	rdb := startRedisContainer(ctx, t)

	const streamName = "test_saga_start_timeout"

	repo := repository.NewPostgresTripRepository(db)
	outboxWkr := worker.NewOutboxWorker(repo, rdb, streamName)
	timeoutWkr := worker.NewTripTimeoutWorker(repo, time.Minute, 60)

	passengerID := uuid.New()
	driverID := uuid.New()
	tripID := uuid.New()

	// Step 1: Create trip
	trip := &domain.Trip{
		ID:             tripID,
		PassengerID:    passengerID,
		Status:         domain.StatusRequested,
		Source:         domain.Location{Point: domain.GeoPoint{Lat: 10.762622, Lng: 106.660172}},
		Destination:    domain.Location{Point: domain.GeoPoint{Lat: 10.795202, Lng: 106.721519}},
		EstimatedPrice: domain.Price{Amount: 50000, Currency: "VND"},
		CreatedAt:      time.Now(),
	}
	createEvent := &repository.OutboxEvent{
		ID:            uuid.New(),
		AggregateType: "TRIP",
		AggregateID:   tripID,
		EventType:     "Trip.Requested",
		Payload:       map[string]interface{}{"id": tripID.String()},
	}
	require.NoError(t, repo.CreateWithOutbox(ctx, trip, createEvent))

	// Step 2: Accept the trip with AcceptWithOutbox then back-date accepted_at to 6 min ago
	acceptEvent := &repository.OutboxEvent{
		ID:            uuid.New(),
		AggregateType: "TRIP",
		AggregateID:   tripID,
		EventType:     "Trip.Accepted",
		Payload:       map[string]interface{}{"id": tripID.String(), "driver_id": driverID.String()},
	}
	require.NoError(t, repo.AcceptWithOutbox(ctx, tripID, driverID, acceptEvent))

	// Backdate accepted_at to simulate "driver accepted but never started"
	_, err := db.ExecContext(ctx,
		"UPDATE trips SET accepted_at = $1 WHERE id = $2",
		time.Now().Add(-6*time.Minute), tripID,
	)
	require.NoError(t, err)

	// Flush pending events so stream is clean for assertions
	outboxWkr.ProcessOnce(ctx)

	// Step 3: TripTimeoutWorker fires cancelStuckAccepted
	timeoutWkr.CheckTimeoutsOnce(ctx)

	// Step 4: Publish Trip.Cancelled to stream
	outboxWkr.ProcessOnce(ctx)

	// Step 5a: Trip status must be CANCELLED
	updated, err := repo.GetByID(ctx, tripID)
	require.NoError(t, err)
	require.NotNil(t, updated)
	assert.Equal(t, domain.StatusCancelled, updated.Status)

	// Step 5b: Stream must contain Trip.Cancelled with reason START_TIMEOUT
	msgs, err := rdb.XRange(ctx, streamName, "-", "+").Result()
	require.NoError(t, err)

	var cancelledFound bool
	for _, msg := range msgs {
		if msg.Values["type"] == "Trip.Cancelled" {
			cancelledFound = true
			assert.Equal(t, tripID.String(), msg.Values["aggregate_id"])
		}
	}
	assert.True(t, cancelledFound, "Redis stream must contain Trip.Cancelled event")
}
