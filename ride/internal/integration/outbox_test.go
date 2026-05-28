//go:build integration

package integration_test

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

// rideSchema is the complete DDL for the rides schema applied inline.
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

func startPostgresContainer(ctx context.Context, t *testing.T) *sql.DB {
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

func startRedisContainer(ctx context.Context, t *testing.T) *redis.Client {
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

// TestOutboxAtomicity verifies the Outbox pattern's atomicity guarantees:
//  1. A committed trip+outbox_event row is picked up by the worker and lands in Redis.
//  2. A rolled-back TX leaves no row in outbox_events, so the worker publishes nothing.
func TestOutboxAtomicity(t *testing.T) {
	ctx := context.Background()

	db := startPostgresContainer(ctx, t)
	rdb := startRedisContainer(ctx, t)

	const streamName = "test_ride_events_outbox"
	repo := repository.NewPostgresTripRepository(db)
	outboxWkr := worker.NewOutboxWorker(repo, rdb, streamName)

	t.Run("committed trip+event appears in Redis stream", func(t *testing.T) {
		tripID := uuid.New()
		trip := &domain.Trip{
			ID:             tripID,
			PassengerID:    uuid.New(),
			Status:         domain.StatusRequested,
			Source:         domain.Location{Point: domain.GeoPoint{Lat: 10.762622, Lng: 106.660172}},
			Destination:    domain.Location{Point: domain.GeoPoint{Lat: 10.795202, Lng: 106.721519}},
			EstimatedPrice: domain.Price{Amount: 50000, Currency: "VND"},
			CreatedAt:      time.Now(),
		}
		event := &repository.OutboxEvent{
			ID:            uuid.New(),
			AggregateType: "TRIP",
			AggregateID:   tripID,
			EventType:     "Trip.Requested",
			Payload:       map[string]interface{}{"id": tripID.String()},
		}

		require.NoError(t, repo.CreateWithOutbox(ctx, trip, event))

		// Run one poll cycle — expect exactly one PENDING event
		outboxWkr.ProcessOnce(ctx)

		msgs, err := rdb.XRange(ctx, streamName, "-", "+").Result()
		require.NoError(t, err)
		require.Len(t, msgs, 1)
		assert.Equal(t, "Trip.Requested", msgs[0].Values["type"])
		assert.Equal(t, tripID.String(), msgs[0].Values["aggregate_id"])

		// Event should now be PUBLISHED (no longer PENDING)
		outboxWkr.ProcessOnce(ctx)
		msgs2, _ := rdb.XRange(ctx, streamName, "-", "+").Result()
		assert.Len(t, msgs2, 1, "second poll must not re-publish the same event")
	})

	t.Run("rolled-back TX produces no Redis event", func(t *testing.T) {
		// Use a separate stream to avoid bleed from the committed-event subtest
		const rollbackStream = "test_ride_events_rollback"
		rollbackWorker := worker.NewOutboxWorker(repo, rdb, rollbackStream)

		tx, err := db.BeginTx(ctx, nil)
		require.NoError(t, err)

		tripID := uuid.New()
		_, err = tx.ExecContext(ctx, `
			INSERT INTO trips
			  (id, passenger_id, status, source_lat, source_lng, dest_lat, dest_lng, estimated_price)
			VALUES ($1, $2, 'REQUESTED', 0, 0, 0, 0, 100)`,
			tripID, uuid.New())
		require.NoError(t, err)

		_, err = tx.ExecContext(ctx, `
			INSERT INTO outbox_events (id, aggregate_type, aggregate_id, event_type, payload)
			VALUES ($1, 'TRIP', $2, 'Trip.Requested', '{}')`,
			uuid.New(), tripID)
		require.NoError(t, err)

		require.NoError(t, tx.Rollback())

		rollbackWorker.ProcessOnce(ctx)

		msgs, err := rdb.XRange(ctx, rollbackStream, "-", "+").Result()
		require.NoError(t, err)
		assert.Len(t, msgs, 0, "rolled-back TX must not produce a Redis event")
	})
}
