-- name: CreateTrip :exec
INSERT INTO trips (
    id, passenger_id, driver_id, status, 
    source_lat, source_lng, dest_lat, dest_lng, 
    estimated_price, currency, source_address, dest_address, created_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13);

-- name: GetTrip :one
SELECT * FROM trips WHERE id = $1 LIMIT 1;

-- name: UpdateTripStatus :exec
UPDATE trips 
SET status = $2
WHERE id = $1;

-- name: UpdateTripDriver :exec
UPDATE trips 
SET driver_id = $2
WHERE id = $1;

-- name: AcceptTrip :exec
UPDATE trips
SET driver_id = $2, status = $3, accepted_at = NOW()
WHERE id = $1;

-- name: CompleteTrip :exec
UPDATE trips
SET status = $2, final_price = $3, completed_at = NOW()
WHERE id = $1;

-- name: CreateOutboxEvent :exec
INSERT INTO outbox_events (id, aggregate_type, aggregate_id, event_type, payload, status, created_at, correlation_id, traceparent)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9);

-- name: GetUnpublishedEvents :many
SELECT id, aggregate_type, aggregate_id, event_type, payload, status, created_at, correlation_id, traceparent FROM outbox_events
WHERE (status = 'PENDING' OR status = 'FAILED')
ORDER BY created_at ASC
LIMIT $1::int;


-- name: GetStuckTrips :many
SELECT * FROM trips 
WHERE status = 'REQUESTED' AND created_at < $1;

-- name: UpdateEventStatus :exec

UPDATE outbox_events 
SET status = $1 
WHERE id = $2;

-- name: IsEventProcessed :one
SELECT EXISTS(SELECT 1 FROM inbox_events WHERE id = $1);

-- name: MarkEventProcessed :exec
INSERT INTO inbox_events (id, event_type) VALUES ($1, $2);

-- name: SetOfferDeadline :exec
UPDATE trips SET offer_deadline = $2 WHERE id = $1;

-- name: GetExpiredOffers :many
SELECT * FROM trips
WHERE driver_id IS NOT NULL
  AND status = 'REQUESTED'
  AND offer_deadline IS NOT NULL
  AND offer_deadline < $1;

-- name: GetStuckAcceptedTrips :many
SELECT * FROM trips
WHERE status = 'ACCEPTED'
  AND accepted_at IS NOT NULL
  AND accepted_at < $1;
