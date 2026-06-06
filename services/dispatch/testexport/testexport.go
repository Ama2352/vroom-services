// Package testexport re-exports dispatch internals for cross-module integration tests.
// Do NOT use in production code.
package testexport

import (
	"github.com/redis/go-redis/v9"

	"vroom-mvp/dispatch/internal/service"
	"vroom-mvp/dispatch/internal/worker"
)

// ── Service ────────────────────────────────────────────────────────────────

type DispatchService = service.DispatchService

func NewDispatchService(rdb *redis.Client) *DispatchService {
	return service.NewDispatchService(rdb)
}

// ── Workers ────────────────────────────────────────────────────────────────

type RideEventConsumer = worker.RideEventConsumer

func NewRideEventConsumer(
	rdb *redis.Client,
	svc *DispatchService,
	stream, group, consumer string,
) *RideEventConsumer {
	return worker.NewRideEventConsumer(rdb, svc, stream, group, consumer)
}
