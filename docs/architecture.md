# Architecture & Patterns

## Service Map

| Service | Port | Database | Key responsibility |
|---------|------|----------|--------------------|
| **user** | 8081 | PostgreSQL (`search_path=users`) | JWT RS256 auth, user CRUD |
| **ride** | 8082 | PostgreSQL (`search_path=rides`) + Redis Streams | Trip lifecycle, Outbox publisher |
| **dispatch** | 8083 | Redis only | Driver geo-matching, Saga coordinator |
| **notification** | 8084 | PostgreSQL (`search_path=notifications`) + Redis Streams | Event consumer, WebSocket push |

All services share one PostgreSQL instance via `search_path` schema isolation. Each service has its own Redis consumer group on the `ride_events` stream.

---

## Async Event Flow

```
Ride Service
  └── writes domain event to outbox_events (same DB tx as trip update)
        │
  OutboxWorker (polls every 2s)
        │ XADD ride_events
        ▼
  Redis Stream: ride_events
        ├── dispatch_group  →  Dispatch Service (geo assignment, Saga)
        └── notification_group  →  Notification Service (WebSocket push)
```

**Why Outbox?** Prevents dual-write: if Redis publish fails after a DB commit, the OutboxWorker retries. Events are never lost.

---

## Trip State Machine

```
REQUESTED → ACCEPTED → IN_PROGRESS → COMPLETED
         ↘ CANCELLED  (from REQUESTED or ACCEPTED only)
```

State transitions are methods on `ride/internal/domain.Trip`. Invalid transitions return `ErrInvalidTripStatus`.

---

## Saga Pattern (offer timeout + compensation)

When dispatch assigns a driver, the Saga ensures both participants stay consistent:

1. **Ride** sets `offer_deadline = now + 10s` on the trip row
2. **Dispatch** marks driver as `ON_OFFER` in Redis
3. If driver rejects or deadline passes → Ride cancels offer, Dispatch releases driver (`ReleaseDriver`)
4. ACCEPTED start-timeout: if trip stays ACCEPTED for > 5 min, `TripTimeoutWorker` cancels it
5. XAUTOCLAIM (30s PEL): if Dispatch crashes mid-offer, the message is reclaimed and reprocessed

---

## Authentication

User service issues **JWT RS256** tokens. Private/public key pair is ephemeral on startup in dev (tokens invalidated on restart). In production, mount keys from Sealed Secrets.

Ride, Dispatch, Notification validate via `JWT_PUBLIC_KEY_PEM` env var. Passthrough if unset (local dev).

---

## Driver Location

Stored in **Redis Geo** (`drivers:available`) via `PUT /v1/drivers/:id/location` or WebSocket. Matching uses `GeoSearchLocation` with a 5 km radius, nearest-first waterfall.
