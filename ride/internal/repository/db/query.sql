-- name: CreateTrip :exec
INSERT INTO trips (
    id, passenger_id, driver_id, status, 
    source_lat, source_lng, dest_lat, dest_lng, 
    estimated_price, created_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10);

-- name: GetTrip :one
SELECT * FROM trips WHERE id = $1 LIMIT 1;

-- name: UpdateTripStatus :exec
UPDATE trips 
SET status = $2
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
INSERT INTO outbox_events (id, aggregate_type, aggregate_id, event_type, payload, status, created_at)
VALUES ($1, $2, $3, $4, $5, $6, $7);

-- name: GetUnpublishedEvents :many
SELECT * FROM outbox_events 
WHERE status = 'PENDING' 
ORDER BY created_at ASC 
LIMIT $1::int;

-- name: UpdateEventStatus :exec
UPDATE outbox_events 
SET status = $1 
WHERE id = $2;
