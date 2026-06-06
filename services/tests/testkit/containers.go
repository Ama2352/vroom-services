package testkit

import (
	"context"
	"database/sql"
	"fmt"
	"testing"
	"time"

	_ "github.com/lib/pq"
	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/require"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"
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

// StartPostgres starts a postgres:15-alpine testcontainer, creates the rides schema,
// and returns a ready *sql.DB. Calls t.Cleanup to terminate the container.
func StartPostgres(ctx context.Context, t *testing.T) *sql.DB {
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
	t.Cleanup(func() { pgC.Terminate(ctx) }) //nolint:errcheck

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
	t.Cleanup(func() { db.Close() }) //nolint:errcheck

	require.Eventually(t, func() bool { return db.Ping() == nil },
		30*time.Second, 500*time.Millisecond, "postgres not ready")

	_, err = db.ExecContext(ctx, rideSchema)
	require.NoError(t, err)

	return db
}

// StartRedis starts a redis:7-alpine testcontainer and returns a ready *redis.Client.
// Calls t.Cleanup to terminate the container.
func StartRedis(ctx context.Context, t *testing.T) *redis.Client {
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
	t.Cleanup(func() { redisC.Terminate(ctx) }) //nolint:errcheck

	host, err := redisC.Host(ctx)
	require.NoError(t, err)
	port, err := redisC.MappedPort(ctx, "6379")
	require.NoError(t, err)

	rdb := redis.NewClient(&redis.Options{Addr: fmt.Sprintf("%s:%s", host, port.Port())})
	t.Cleanup(func() { rdb.Close() }) //nolint:errcheck

	require.Eventually(t, func() bool { return rdb.Ping(ctx).Err() == nil },
		15*time.Second, 300*time.Millisecond, "redis not ready")

	return rdb
}
