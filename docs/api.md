# API Reference

Every service exposes `GET /health`, `GET /readyz`, and `GET /metrics` (Prometheus).

---

## User Service — port 8081

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/auth/register` | Register passenger or driver |
| POST | `/v1/auth/login` | Returns JWT RS256 token |
| GET | `/v1/auth/public-key` | Returns RSA public key PEM (used by other services) |
| GET | `/v1/users/:id` | Get user profile |
| PUT | `/v1/users/:id` | Update user profile |

---

## Ride Service — port 8082

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/trips` | Request a new trip (passenger) |
| GET | `/v1/trips/:id` | Get trip details |
| POST | `/v1/trips/:id/cancel` | Cancel trip (REQUESTED or ACCEPTED only) |
| PUT | `/v1/trips/:id/status` | Internal — update trip status |
| GET | `/v1/trips/history` | Passenger trip history |

---

## Dispatch Service — port 8083

| Method | Path | Description |
|--------|------|-------------|
| PUT | `/v1/drivers/:id/location` | Update driver GPS (REST) |
| POST | `/v1/drivers/:id/offer/accept` | Driver accepts trip offer |
| POST | `/v1/drivers/:id/offer/reject` | Driver rejects trip offer |
| POST | `/v1/trips/:id/start` | Driver starts trip |
| POST | `/v1/trips/:id/complete` | Driver completes trip |
| GET | `/v1/dispatch/ws/location` | WebSocket — stream location updates |

---

## Notification Service — port 8084

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/notifications` | List notifications for user |
| GET | `/v1/ws` | WebSocket — real-time trip status push |
