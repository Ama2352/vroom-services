# Architectural Discrepancy Report: Database Isolation

## 🔍 Overview
This document highlights a discrepancy between the project's documentation (`project-mapping.md`) and the actual technical implementation regarding the "Database per Service" pattern.

## 🚩 The Claim
According to `project-mapping.md`, the following is marked as accomplished:
> **[x] Database per Service:** Isolated PostgreSQL schemas for User and Ride services.

## 🛠 The Reality (Actual Implementation)
As of 2026-04-27, the project is actually using a **Shared Database and Shared Schema** (Logical Monolith Database) architecture.

### 1. Shared Connection Strings
Both the `user-service` and `ride-service` in `docker-compose.yml` point to the exact same database instance and database name without schema isolation:
- **DB_NAME:** `vroom`
- **Schema:** Default (`public`)

### 2. Table Collision & Redundancy
Both services attempt to create the same `outbox_events` table in the `public` schema.
- **User Migrations:** `migrations/000001_init_schema.up.sql` creates `outbox_events`.
- **Ride Migrations:** `migrations/000001_init_ride_schema.up.sql` creates `outbox_events`.

In a true "Database per Service" model, these tables would exist in separate namespaces (e.g., `user_service.outbox_events` and `ride_service.outbox_events`) or entirely separate databases.

### 3. Lack of Schema Definitions
- No `CREATE SCHEMA` commands exist in the migration files or the `init-db.sql` script.
- The Go service connection strings (`dsn`) do not specify a `search_path` to isolate operations to a specific schema.

### 4. Cross-Service Data Coupling
The `notification-service` queries a global `event_logs` table initialized by `scripts/init-db.sql`. This table acts as a shared sink for events, which contradicts the principle of service autonomy where services should only interact via defined APIs or Event Streams (Redis), not a shared database table.

## 📉 Impact
*   **Scalability:** Services cannot be migrated to separate database instances easily.
*   **Reliability:** A migration error in one service could potentially drop tables used by another.
*   **Development:** Schema changes in the "shared" `outbox_events` table could cause unexpected failures across multiple services.

## 🚀 Recommended Remediation
To align the implementation with the intended "Database per Service" architecture:

1.  **Schema Isolation:** Update migrations to create and use dedicated schemas (e.g., `user_schema` and `ride_schema`).
2.  **Search Path:** Update the DSN in each service to include `search_path=your_schema_name`.
3.  **Dedicated Outbox Workers:** Ensure each service manages its own isolated outbox table and polling logic.
4.  **Refactor Notification Service:** The notification service should populate its own internal "history" database by consuming Redis Streams, rather than reading from a shared `event_logs` table.

---
**Status:** Implementation out of sync with Documentation.
**Date:** 2026-04-27
