// Package testexport re-exports ride internals for cross-module integration tests.
// Do NOT use in production code.
package testexport

import (
	"database/sql"
	"time"

	"github.com/redis/go-redis/v9"

	"vroom-mvp/ride/internal/domain"
	"vroom-mvp/ride/internal/repository"
	"vroom-mvp/ride/internal/worker"
)

// ── Domain ─────────────────────────────────────────────────────────────────

type Trip = domain.Trip
type TripStatus = domain.TripStatus
type Location = domain.Location
type GeoPoint = domain.GeoPoint
type Price = domain.Price

const (
	StatusRequested = domain.StatusRequested
	StatusAccepted  = domain.StatusAccepted
	StatusStarted   = domain.StatusStarted
	StatusCompleted = domain.StatusCompleted
	StatusCancelled = domain.StatusCancelled
)

// ── Repository ─────────────────────────────────────────────────────────────

type TripRepository = repository.TripRepository
type PostgresTripRepository = repository.PostgresTripRepository
type OutboxEvent = repository.OutboxEvent

func NewPostgresTripRepository(conn *sql.DB) *PostgresTripRepository {
	return repository.NewPostgresTripRepository(conn)
}

// ── Workers ────────────────────────────────────────────────────────────────

type OutboxWorker = worker.OutboxWorker
type TripTimeoutWorker = worker.TripTimeoutWorker
type TripUpdateWorker = worker.TripUpdateWorker

func NewOutboxWorker(repo TripRepository, rdb *redis.Client, stream string) *OutboxWorker {
	return worker.NewOutboxWorker(repo, rdb, stream)
}

func NewTripTimeoutWorker(repo TripRepository, interval time.Duration, timeoutSec int) *TripTimeoutWorker {
	return worker.NewTripTimeoutWorker(repo, interval, timeoutSec)
}

func NewTripUpdateWorker(rdb *redis.Client, repo TripRepository, stream, group, consumer string) *TripUpdateWorker {
	return worker.NewTripUpdateWorker(rdb, repo, stream, group, consumer)
}
