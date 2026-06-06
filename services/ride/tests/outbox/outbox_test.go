//go:build integration

package outbox_test

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
// Test 1: TestOutbox_AtomicityCommit
// ---------------------------------------------------------------------------

func TestOutbox_AtomicityCommit(t *testing.T) {
	logBegin(t, "TestOutbox_AtomicityCommit",
		"Committed TX → event published to Redis stream; status = PUBLISHED")

	ctx := context.Background()

	logStep(t, 1, 4, "Spin up Postgres + Redis containers")
	db := startPostgres(ctx, t)
	rdb := startRedis(ctx, t)

	const streamName = "test_outbox_commit_events"
	repo := repository.NewPostgresTripRepository(db)
	outboxWkr := worker.NewOutboxWorker(repo, rdb, streamName)

	logStep(t, 2, 4, "Create trip + outbox event via CreateWithOutbox (committed TX)")
	tripID := uuid.New()
	trip := newTrip(tripID)
	event := newOutboxEvent(tripID, "Trip.Requested")
	logDetail(t, "tripID", tripID.String())
	logDetail(t, "eventID", event.ID.String())
	logDetail(t, "eventType", event.EventType)

	require.NoError(t, repo.CreateWithOutbox(ctx, trip, event))
	logArrow(t, "CreateWithOutbox succeeded — trip + event committed atomically")

	logStep(t, 3, 4, "OutboxWorker.ProcessOnce() — first poll")
	outboxWkr.ProcessOnce(ctx)
	logArrow(t, "ProcessOnce completed")

	msgs, err := rdb.XRange(ctx, streamName, "-", "+").Result()
	require.NoError(t, err)
	require.Len(t, msgs, 1, "expected exactly 1 stream entry after first poll")
	assert.Equal(t, "Trip.Requested", msgs[0].Values["type"])
	assert.Equal(t, tripID.String(), msgs[0].Values["aggregate_id"])
	logArrow(t, "stream entry verified: type=Trip.Requested, aggregate_id matches")

	logStep(t, 4, 4, "OutboxWorker.ProcessOnce() — second poll (idempotency check)")
	outboxWkr.ProcessOnce(ctx)
	msgs2, err := rdb.XRange(ctx, streamName, "-", "+").Result()
	require.NoError(t, err)
	assert.Len(t, msgs2, 1, "second poll must not re-publish the already-PUBLISHED event")
	logArrow(t, "still exactly 1 entry — no re-publish occurred")

	logResult(t,
		"PASS committed TX produced exactly 1 stream entry",
		"PASS second ProcessOnce() did not re-publish (event is PUBLISHED)",
	)
}

// ---------------------------------------------------------------------------
// Test 2: TestOutbox_AtomicityRollback
// ---------------------------------------------------------------------------

func TestOutbox_AtomicityRollback(t *testing.T) {
	logBegin(t, "TestOutbox_AtomicityRollback",
		"Rolled-back TX → no stream entry")

	ctx := context.Background()

	logStep(t, 1, 3, "Spin up Postgres + Redis containers")
	db := startPostgres(ctx, t)
	rdb := startRedis(ctx, t)

	const streamName = "test_outbox_rollback_events"
	repo := repository.NewPostgresTripRepository(db)
	outboxWkr := worker.NewOutboxWorker(repo, rdb, streamName)

	logStep(t, 2, 3, "Begin manual TX, INSERT trip + outbox_event, then ROLLBACK")
	tx, err := db.BeginTx(ctx, nil)
	require.NoError(t, err)

	tripID := uuid.New()
	logDetail(t, "tripID", tripID.String())

	_, err = tx.ExecContext(ctx, `
		INSERT INTO trips
		  (id, passenger_id, status, source_lat, source_lng, dest_lat, dest_lng, estimated_price)
		VALUES ($1, $2, 'REQUESTED', 0, 0, 0, 0, 100)`,
		tripID, uuid.New())
	require.NoError(t, err)

	eventID := uuid.New()
	_, err = tx.ExecContext(ctx, `
		INSERT INTO outbox_events (id, aggregate_type, aggregate_id, event_type, payload)
		VALUES ($1, 'TRIP', $2, 'Trip.Requested', '{}')`,
		eventID, tripID)
	require.NoError(t, err)

	require.NoError(t, tx.Rollback())
	logArrow(t, "TX rolled back — no rows committed")

	logStep(t, 3, 3, "OutboxWorker.ProcessOnce() — should find nothing to publish")
	outboxWkr.ProcessOnce(ctx)

	msgs, err := rdb.XRange(ctx, streamName, "-", "+").Result()
	require.NoError(t, err)
	assert.Len(t, msgs, 0, "rolled-back TX must not produce a Redis event")
	logArrow(t, "stream is empty — rollback atomicity confirmed")

	logResult(t,
		"PASS rolled-back TX left 0 stream entries",
	)
}

// ---------------------------------------------------------------------------
// Test 3: TestOutbox_FailedStatusRetry
// ---------------------------------------------------------------------------

func TestOutbox_FailedStatusRetry(t *testing.T) {
	logBegin(t, "TestOutbox_FailedStatusRetry",
		"Redis publish failure → event marked FAILED → next ProcessOnce() retries and PUBLISHES")

	ctx := context.Background()

	logStep(t, 1, 6, "Spin up Postgres + Redis containers")
	db := startPostgres(ctx, t)
	rdb := startRedis(ctx, t)

	const streamName = "test_outbox_retry_events"
	repo := repository.NewPostgresTripRepository(db)

	logStep(t, 2, 6, "Create trip + outbox event via CreateWithOutbox (status = PENDING)")
	tripID := uuid.New()
	trip := newTrip(tripID)
	event := newOutboxEvent(tripID, "Trip.Requested")
	logDetail(t, "tripID", tripID.String())
	logDetail(t, "eventID", event.ID.String())

	require.NoError(t, repo.CreateWithOutbox(ctx, trip, event))
	logArrow(t, "event created with status PENDING")

	logStep(t, 3, 6, "Create bad Redis client (wrong port) for a worker that will fail to publish")
	badRdb := redis.NewClient(&redis.Options{Addr: "127.0.0.1:19999"})
	t.Cleanup(func() { badRdb.Close() })
	badWorker := worker.NewOutboxWorker(repo, badRdb, streamName)

	logStep(t, 4, 6, "ProcessOnce() on bad worker → publish fails → event marked FAILED")
	badWorker.ProcessOnce(ctx)
	logArrow(t, "bad worker ProcessOnce completed (expected failure logged above)")

	var status string
	err := db.QueryRowContext(ctx,
		"SELECT status FROM outbox_events WHERE id = $1", event.ID).Scan(&status)
	require.NoError(t, err)
	assert.Equal(t, "FAILED", status, "event must be marked FAILED after publish error")
	logDetail(t, "DB status", status)

	logStep(t, 5, 6, "Create good OutboxWorker (correct Redis client)")
	goodWorker := worker.NewOutboxWorker(repo, rdb, streamName)

	logStep(t, 6, 6, "ProcessOnce() on good worker → FAILED event is retried and PUBLISHED")
	goodWorker.ProcessOnce(ctx)
	logArrow(t, "good worker ProcessOnce completed")

	msgs, err := rdb.XRange(ctx, streamName, "-", "+").Result()
	require.NoError(t, err)
	assert.Len(t, msgs, 1, "FAILED event must be retried and appear in stream")
	logArrow(t, "stream has 1 entry — FAILED event was successfully retried")

	logResult(t,
		"PASS bad Redis client caused event to be marked FAILED",
		"PASS good worker retried FAILED event and published it to stream",
	)
}

// ---------------------------------------------------------------------------
// Test 4: TestOutbox_DuplicatePublishPrevented
// ---------------------------------------------------------------------------

func TestOutbox_DuplicatePublishPrevented(t *testing.T) {
	logBegin(t, "TestOutbox_DuplicatePublishPrevented",
		"ProcessOnce() called twice on same event → only 1 stream entry")

	ctx := context.Background()

	logStep(t, 1, 4, "Spin up Postgres + Redis containers")
	db := startPostgres(ctx, t)
	rdb := startRedis(ctx, t)

	const streamName = "test_outbox_dedup_events"
	repo := repository.NewPostgresTripRepository(db)
	outboxWkr := worker.NewOutboxWorker(repo, rdb, streamName)

	logStep(t, 2, 4, "Create trip + outbox event via CreateWithOutbox")
	tripID := uuid.New()
	trip := newTrip(tripID)
	event := newOutboxEvent(tripID, "Trip.Requested")
	logDetail(t, "tripID", tripID.String())
	logDetail(t, "eventID", event.ID.String())

	require.NoError(t, repo.CreateWithOutbox(ctx, trip, event))
	logArrow(t, "event created with status PENDING")

	logStep(t, 3, 4, "ProcessOnce() #1 → event marked PUBLISHED → 1 entry in stream")
	outboxWkr.ProcessOnce(ctx)

	msgs, err := rdb.XRange(ctx, streamName, "-", "+").Result()
	require.NoError(t, err)
	require.Len(t, msgs, 1, "expected exactly 1 entry after first ProcessOnce")
	logArrow(t, "stream has 1 entry after first poll")

	logStep(t, 4, 4, "ProcessOnce() #2 → PUBLISHED event is not fetched again")
	outboxWkr.ProcessOnce(ctx)

	msgs2, err := rdb.XRange(ctx, streamName, "-", "+").Result()
	require.NoError(t, err)
	assert.Len(t, msgs2, 1, "second ProcessOnce must not add a duplicate stream entry")
	logArrow(t, "stream still has exactly 1 entry — no duplicate published")

	logResult(t,
		"PASS ProcessOnce() twice produced exactly 1 stream entry",
		"PASS PUBLISHED event is not re-fetched by GetUnpublishedEvents",
	)
}

// ---------------------------------------------------------------------------
// Test 5: TestOutbox_CorrelationIdPropagated
// ---------------------------------------------------------------------------

func TestOutbox_CorrelationIdPropagated(t *testing.T) {
	logBegin(t, "TestOutbox_CorrelationIdPropagated",
		"correlation_id set on outbox event → appears unchanged in Redis stream entry")

	ctx := context.Background()

	logStep(t, 1, 4, "Spin up Postgres + Redis containers")
	db := startPostgres(ctx, t)
	rdb := startRedis(ctx, t)

	const streamName = "test_outbox_correlation_events"
	repo := repository.NewPostgresTripRepository(db)
	outboxWkr := worker.NewOutboxWorker(repo, rdb, streamName)

	logStep(t, 2, 4, "Create outbox event with a known CorrelationID")
	tripID := uuid.New()
	trip := newTrip(tripID)
	knownCorrelationID := uuid.New().String()
	event := newOutboxEvent(tripID, "Trip.Requested")
	event.CorrelationID = knownCorrelationID
	logDetail(t, "tripID", tripID.String())
	logDetail(t, "correlID", knownCorrelationID)

	require.NoError(t, repo.CreateWithOutbox(ctx, trip, event))
	logArrow(t, "event committed with CorrelationID set")

	logStep(t, 3, 4, "OutboxWorker.ProcessOnce() — publish to stream")
	outboxWkr.ProcessOnce(ctx)
	logArrow(t, "ProcessOnce completed")

	logStep(t, 4, 4, "XRange the stream and assert correlation_id is propagated")
	msgs, err := rdb.XRange(ctx, streamName, "-", "+").Result()
	require.NoError(t, err)
	require.Len(t, msgs, 1, "expected exactly 1 stream entry")

	gotCorrelationID := msgs[0].Values["correlation_id"]
	assert.Equal(t, knownCorrelationID, gotCorrelationID,
		"correlation_id in stream must match what was set on the OutboxEvent")
	logDetail(t, "stream val", fmt.Sprintf("%v", gotCorrelationID))

	logResult(t,
		"PASS correlation_id propagated from OutboxEvent through Redis stream entry unchanged",
	)
}
