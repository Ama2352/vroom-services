# Database Architecture Remediation Plan

This document outlines the steps to align the current implementation with the **"Database per Service"** architecture defined in the master project plans.

## 🚩 Current Issues
1. **Schema Collision:** Both `user-service` and `ride-service` use the `public` schema, causing collisions on the `outbox_events` table.
2. **Global Coupling:** `notification-service` reads from a shared `event_logs` table initialized in `init-db.sql`.
3. **Shared Credentials:** All services use the same `vroom_dev` password and `vroom` database name without isolation.

---

## 🛠 Remediation Steps

### Phase 1: PostgreSQL Initialization (`scripts/init-db.sql`)
**Goal:** Establish logical isolation at the database level.

1. **Create Schemas:**
   ```sql
   CREATE SCHEMA IF NOT EXISTS users;
   CREATE SCHEMA IF NOT EXISTS rides;
   CREATE SCHEMA IF NOT EXISTS notifications;
   CREATE SCHEMA IF NOT EXISTS dispatch;
   ```
2. **Create Service Users:**
   - Create `user_svc`, `ride_svc`, `notification_svc`, and `dispatch_svc`.
   - Grant `USAGE` and `CREATE` on their respective schemas only.
   - Set `search_path` for each user to their specific schema.

### Phase 2: Migration Refactoring
**Goal:** Isolate table definitions per service.

1. **User Service:** Update `user/migrations/000001_init_schema.up.sql` to explicitly use `users.` prefix or rely on the `user_svc` search path.
2. **Ride Service:** Update `ride/migrations/000001_init_ride_schema.up.sql` to ensure it targets the `rides` schema.
3. **Outbox Autonomy:** Accept that both services have their own `outbox_events` table. This is **correct** in microservices as it prevents shared-table coupling.

### Phase 3: Docker-Compose & DSN Updates
**Goal:** Connect services to their dedicated "logical" databases.

1. **Update `docker-compose.yml`:**
   - Change `DB_USER` for each service to its dedicated user.
   - Add `search_path` to connection strings:
     ```yaml
     DB_DSN: "postgres://user_svc:password@postgres:5432/vroom?sslmode=disable&search_path=users"
     ```

### Phase 4: Notification Service Refactor
**Goal:** Transition from DB-sharing to Event-driven.

1. **Remove Shared Sink:** Delete the `event_logs` table from `init-db.sql`.
2. **Implement Persistence:** Update `notification-service` to:
   - Consume events from **Redis Streams** (`ride_events`).
   - Store processed notification history in its own `notifications` schema.
   - Implement **Idempotency** using an `idempotency_key` (e.g., `event_id`) in its local database.

---

## 📊 Expected Architectural State

| Service | Schema | Database User | Isolation Type |
|---------|--------|---------------|----------------|
| **User** | `users` | `user_svc` | Schema-based |
| **Ride** | `rides` | `ride_svc` | Schema-based |
| **Dispatch** | `dispatch` | `dispatch_svc` | Schema-based |
| **Notification** | `notifications` | `notification_svc` | Schema-based |

---

## ✅ Success Criteria
- [ ] `docker-compose up` initializes 4 distinct schemas.
- [ ] `user-service` migrations do not interfere with `ride-service`.
- [ ] `notification-service` successfully pushes WebSockets based ONLY on Redis Stream events.
- [ ] No service has permission to `SELECT` or `JOIN` tables from another schema.

---
**Prepared By:** Antigravity AI
**Date:** 2026-04-27
