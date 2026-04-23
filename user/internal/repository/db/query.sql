-- name: CreateUser :exec
INSERT INTO users (id, email, password_hash, name, role, created_at)
VALUES ($1, $2, $3, $4, $5, $6);

-- name: GetUserByID :one
SELECT id, email, password_hash, name, role, created_at FROM users
WHERE id = $1 LIMIT 1;

-- name: GetUserByEmail :one
SELECT id, email, password_hash, name, role, created_at FROM users
WHERE email = $1 LIMIT 1;

-- name: UpdateUser :exec
UPDATE users
SET email = $1, name = $2, role = $3
WHERE id = $4;

-- name: CreateOutboxEvent :exec
INSERT INTO outbox_events (id, aggregate_type, aggregate_id, event_type, payload, status, created_at)
VALUES ($1, $2, $3, $4, $5, $6, $7);
