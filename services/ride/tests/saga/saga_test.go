//go:build integration

package saga_test

import (
	"context"
	"database/sql"
	"fmt"
	"testing"
	"time"

	_ "github.com/lib/pq"
	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"

	"vroom-mvp/ride/internal/domain"
	"vroom-mvp/ride/internal/repository"
	"vroom-mvp/ride/internal/worker"
)

// ---------------------------------------------------------------------------
// Logger helpers
// ---------------------------------------------------------------------------

func logBegin(t *testing.T, testName, proves string) {
	t.Helper()
	t.Log("╔══════════════════════════════════════════════════════════════════╗")
	t.Logf("║  TEST    %-56s║", testName)
	t.Logf("║  PROVING %-56s║", proves)
	t.Log("╚══════════════════════════════════════════════════════════════════╝")
	t.Log("")
}

func logStep(t *testing.T, n, total int, label string) {
	t.Helper()
	t.Logf("[STEP %d/%d] %s", n, total, label)
}

func logArrow(t *testing.T, action string) {
	t.Helper()
	t.Logf("           → %s", action)
}

func logDetail(t *testing.T, key, value string) {
	t.Helper()
	t.Logf("           %-12s: %s", key, value)
}

func logResult(t *testing.T, checks ...string) {
	t.Helper()
	t.Log("")
	t.Log("[RESULT]")
	for _, c := range checks {
		t.Logf("         %s", c)
	}
}

// ---------------------------------------------------------------------------
// Schema DDL
// ---------------------------------------------------------------------------

const rideSchema = `
CREATE SCHEMA IF NOT EXISTS rides;
SET search_path = rides;

CREATE TABLE IF NOT EXISTS trips (
    id UUID PRIMARY KEY,
    passenger_id UUID NOT NULL,
    driver_id UUID,
    status VARCHAR(20) NOT NULL,
    source_lat DOUBLE PRECISION NOT NULL,
    source_lng DOUBLE PRECISION NOT NULL,
    dest_lat DOUBLE PRECISION NOT NULL,
    dest_lng DOUBLE PRECISION NOT NULL,
    estimated_price DOUBLE PRECISION NOT NULL,
    final_price DOUBLE PRECISION,
    currency VARCHAR(10) DEFAULT 'VND',
    source_address TEXT,
    dest_address TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    accepted_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    offer_deadline TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS outbox_events (
    id UUID PRIMARY KEY,
    aggregate_type VARCHAR(50) NOT NULL,
    aggregate_id UUID NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    correlation_id VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS inbox_events (
    id UUID PRIMARY KEY,
    event_type VARCHAR(255) NOT NULL,
    processed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
`

// ---------------------------------------------------------------------------
// Container helpers
// ---------------------------------------------------------------------------

func startPostgres(ctx context.Context, t *testing.T) *sql.DB {
	t.Helper()

	pgC, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			Image: "postgres:15-alpine",
			Env: map[string]string{
				"POSTGRES_DB":       "vroom_test",
				"POSTGRES_USER":     "vroom",
				"POSTGRES_PASSWORD": "vroom_test",
			},
			ExposedPorts: []string{"5432/tcp"},
			WaitingFor:   wait.ForLog("database system is ready to accept connections").WithOccurrence(2),
		},
		Started: true,
	})
	require.NoError(t, err)
	t.Cleanup(func() { pgC.Terminate(ctx) })

	host, err := pgC.Host(ctx)
	require.NoError(t, err)
	port, err := pgC.MappedPort(ctx, "5432")
	require.NoError(t, err)

	dsn := fmt.Sprintf(
		"host=%s port=%s user=vroom password=vroom_test dbname=vroom_test sslmode=disable search_path=rides",
		host, port.Port(),
	)
	db, err := sql.Open("postgres", dsn)
	require.NoError(t, err)
	t.Cleanup(func() { db.Close() })

	require.Eventually(t, func() bool { return db.Ping() == nil },
		30*time.Second, 500*time.Millisecond, "postgres not ready")

	_, err = db.ExecContext(ctx, rideSchema)
	require.NoError(t, err)

	return db
}

func startRedis(ctx context.Context, t *testing.T) *redis.Client {
	t.Helper()

	redisC, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			Image:        "redis:7-alpine",
			ExposedPorts: []string{"6379/tcp"},
			WaitingFor:   wait.ForLog("Ready to accept connections"),
		},
		Started: true,
	})
	require.NoError(t, err)
	t.Cleanup(func() { redisC.Terminate(ctx) })

	host, err := redisC.Host(ctx)
	require.NoError(t, err)
	port, err := redisC.MappedPort(ctx, "6379")
	require.NoError(t, err)

	rdb := redis.NewClient(&redis.Options{Addr: fmt.Sprintf("%s:%s", host, port.Port())})
	t.Cleanup(func() { rdb.Close() })

	require.Eventually(t, func() bool { return rdb.Ping(ctx).Err() == nil },
		15*time.Second, 300*time.Millisecond, "redis not ready")

	return rdb
}

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

func newTrip(id uuid.UUID) *domain.Trip {
	return &domain.Trip{
		ID:             id,
		PassengerID:    uuid.New(),
		Status:         domain.StatusRequested,
		Source:         domain.Location{Point: domain.GeoPoint{Lat: 10.762622, Lng: 106.660172}},
		Destination:    domain.Location{Point: domain.GeoPoint{Lat: 10.795202, Lng: 106.721519}},
		EstimatedPrice: domain.Price{Amount: 50000, Currency: "VND"},
		CreatedAt:      time.Now(),
	}
}

func newOutboxEvent(tripID uuid.UUID, eventType string) *repository.OutboxEvent {
	return &repository.OutboxEvent{
		ID:            uuid.New(),
		AggregateType: "TRIP",
		AggregateID:   tripID,
		EventType:     eventType,
		Payload:       map[string]interface{}{"id": tripID.String()},
	}
}

// ---------------------------------------------------------------------------
// Test 1: TestSaga_OfferRejectionChain
// ---------------------------------------------------------------------------

func TestSaga_OfferRejectionChain(t *testing.T) {
	logBegin(t, "TestSaga_OfferRejectionChain",
		"Offer timeout → Trip.OfferRejected compensation → driver cleared")

	ctx := context.Background()

	logStep(t, 1, 5, "Create trip via Outbox")
	db := startPostgres(ctx, t)
	rdb := startRedis(ctx, t)

	const streamName = "test_saga_offer_rejection"

	repo := repository.NewPostgresTripRepository(db)
	outboxWkr := worker.NewOutboxWorker(repo, rdb, streamName)
	timeoutWkr := worker.NewTripTimeoutWorker(repo, time.Minute, 60)

	passengerID := uuid.New()
	driverID := uuid.New()
	tripID := uuid.New()

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
	logArrow(t, "Trip created and persisted via Outbox")
	logDetail(t, "tripID", tripID.String())

	logStep(t, 2, 5, "Simulate Trip.Matched (assign driver + set expired offer_deadline)")
	require.NoError(t, repo.UpdateDriver(ctx, tripID, driverID))
	expiredDeadline := time.Now().Add(-5 * time.Second)
	require.NoError(t, repo.SetOfferDeadline(ctx, tripID, expiredDeadline))
	logArrow(t, "Driver assigned and offer_deadline set to past")
	logDetail(t, "driverID", driverID.String())
	logDetail(t, "deadline", expiredDeadline.Format(time.RFC3339))

	logStep(t, 3, 5, "Flush pending events")
	outboxWkr.ProcessOnce(ctx)
	logArrow(t, "Trip.Requested published to stream")

	logStep(t, 4, 5, "TripTimeoutWorker detects expired offer → compensation")
	timeoutWkr.CheckTimeoutsOnce(ctx)
	logArrow(t, "TripTimeoutWorker fired: Trip.OfferRejected event recorded")

	logStep(t, 5, 5, "OutboxWorker publishes Trip.OfferRejected")
	outboxWkr.ProcessOnce(ctx)
	logArrow(t, "OutboxWorker published compensation event")

	updated, err := repo.GetByID(ctx, tripID)
	require.NoError(t, err)
	require.NotNil(t, updated)

	msgs, err := rdb.XRange(ctx, streamName, "-", "+").Result()
	require.NoError(t, err)

	var offerRejectedFound bool
	for _, msg := range msgs {
		if msg.Values["type"] == "Trip.OfferRejected" {
			offerRejectedFound = true
			assert.Equal(t, tripID.String(), msg.Values["aggregate_id"])
		}
	}

	assert.Equal(t, domain.StatusRequested, updated.Status)
	assert.Nil(t, updated.DriverID, "driver_id must be cleared after offer rejection")
	assert.True(t, offerRejectedFound, "Redis stream must contain Trip.OfferRejected event")

	logResult(t,
		fmt.Sprintf("✓ trip.status      : %s", updated.Status),
		fmt.Sprintf("✓ driver_id cleared: %v", updated.DriverID == nil),
		fmt.Sprintf("✓ Trip.OfferRejected found in stream: %v", offerRejectedFound),
	)
}

// ---------------------------------------------------------------------------
// Test 2: TestSaga_StartTimeout
// ---------------------------------------------------------------------------

func TestSaga_StartTimeout(t *testing.T) {
	logBegin(t, "TestSaga_StartTimeout",
		"ACCEPTED >5 min → Trip.Cancelled (START_TIMEOUT) compensation")

	ctx := context.Background()

	logStep(t, 1, 5, "Create trip")
	db := startPostgres(ctx, t)
	rdb := startRedis(ctx, t)

	const streamName = "test_saga_start_timeout"

	repo := repository.NewPostgresTripRepository(db)
	outboxWkr := worker.NewOutboxWorker(repo, rdb, streamName)
	timeoutWkr := worker.NewTripTimeoutWorker(repo, time.Minute, 60)

	passengerID := uuid.New()
	driverID := uuid.New()
	tripID := uuid.New()

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
	logArrow(t, "Trip created with status REQUESTED")
	logDetail(t, "tripID", tripID.String())

	logStep(t, 2, 5, "Accept trip (AcceptWithOutbox)")
	acceptEvent := &repository.OutboxEvent{
		ID:            uuid.New(),
		AggregateType: "TRIP",
		AggregateID:   tripID,
		EventType:     "Trip.Accepted",
		Payload:       map[string]interface{}{"id": tripID.String(), "driver_id": driverID.String()},
	}
	require.NoError(t, repo.AcceptWithOutbox(ctx, tripID, driverID, acceptEvent))
	logArrow(t, "Trip accepted by driver")
	logDetail(t, "driverID", driverID.String())

	logStep(t, 3, 5, "Backdate accepted_at to 6 min ago")
	_, err := db.ExecContext(ctx,
		"UPDATE trips SET accepted_at = $1 WHERE id = $2",
		time.Now().Add(-6*time.Minute), tripID,
	)
	require.NoError(t, err)
	logArrow(t, "accepted_at backdated to simulate stalled acceptance")
	logDetail(t, "accepted_at", time.Now().Add(-6*time.Minute).Format(time.RFC3339))

	logStep(t, 4, 5, "Flush pending events + TripTimeoutWorker fires cancelStuckAccepted")
	outboxWkr.ProcessOnce(ctx)
	logArrow(t, "Trip.Accepted published to stream")

	timeoutWkr.CheckTimeoutsOnce(ctx)
	logArrow(t, "TripTimeoutWorker fired: Trip.Cancelled (START_TIMEOUT) recorded")

	logStep(t, 5, 5, "OutboxWorker publishes Trip.Cancelled")
	outboxWkr.ProcessOnce(ctx)
	logArrow(t, "OutboxWorker published Trip.Cancelled event")

	updated, err := repo.GetByID(ctx, tripID)
	require.NoError(t, err)
	require.NotNil(t, updated)

	msgs, err := rdb.XRange(ctx, streamName, "-", "+").Result()
	require.NoError(t, err)

	var cancelledFound bool
	for _, msg := range msgs {
		if msg.Values["type"] == "Trip.Cancelled" {
			cancelledFound = true
			assert.Equal(t, tripID.String(), msg.Values["aggregate_id"])
		}
	}

	assert.Equal(t, domain.StatusCancelled, updated.Status)
	assert.True(t, cancelledFound, "Redis stream must contain Trip.Cancelled event")

	logResult(t,
		fmt.Sprintf("✓ trip.status      : %s", updated.Status),
		fmt.Sprintf("✓ Trip.Cancelled found in stream: %v", cancelledFound),
	)
}
