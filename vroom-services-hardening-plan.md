# Vroom Services: Hardening & Implementation Plan

This document outlines the phased plan to bring the `vroom-services` implementation into full alignment with the architectural specifications defined in the project's `.docs`.

---

## Phase 1: Rich Domain Model (DDD Refactor)

**Objective:** Transition from "Anemic Domain Models" to "Rich Domain Models" where business logic, invariants, and state transitions are encapsulated within Aggregate Roots, as specified in `02-ddd-domain-model.md`.

### 1.1. User Context Hardening
*   **Refactor `User` Aggregate Root:**
    *   Introduce `Email` Value Object with regex validation and domain extraction.
    *   Introduce `PhoneNumber` Value Object with country code and verification state.
    *   Introduce `Role` as a type-safe enum.
    *   **Domain Logic:** Move the "Assign Driver" logic into `User.AssignDriver()` method to ensure role consistency invariants (e.g., only passengers can become drivers).
*   **Repository Update:** Update `UserRepository` to use these Value Objects in queries (e.g., `FindByEmail(Email)`).

### 1.2. Ride Context Hardening
*   **Refactor `Trip` Aggregate Root:**
    *   Introduce `Price` Value Object (Amount + Currency).
    *   Introduce `GeoPoint` Value Object (Latitude + Longitude).
    *   Introduce `Location` Entity (Source vs Destination) as a child of Trip.
    *   **Encapsulated State Machine:** 
        *   Implement `Trip.AcceptByDriver(driverID)`: Must check if status is `REQUESTED` and `DriverID` is nil.
        *   Implement `Trip.Start()`: Must check if status is `ACCEPTED`.
        *   Implement `Trip.Complete()`: Must check if status is `IN_PROGRESS`.
    *   **Domain Events:** Add a `recordEvent()` mechanism inside the `Trip` struct so that state transitions automatically prepare `OutboxEvent` data.
*   **Repository Update:** Update `TripRepository.Save()` to handle the complex Aggregate (Trip + Locations).

### 1.3. Dispatch Context Modeling
*   **Formalize `DriverPool` Aggregate:**
    *   Even though it's Redis-backed, create a `DriverPool` domain model in `dispatch/internal/domain`.
    *   Encapsulate the "Waterfall Match" algorithm logic as a domain service or method on the pool.
    *   Introduce `AvailableDriver` Value Object to represent a snapshot of driver state.

### 1.4. Notification Context Initialization
*   **Create Domain Layer:**
    *   Define `Notification` Aggregate Root.
    *   Value Objects: `NotificationType` (RIDE_OFFERED, RIDE_ASSIGNED, etc.), `NotificationStatus` (QUEUED, SENT, DELIVERED, FAILED), and `DeliveryChannel` (WEBSOCKET).
    *   **Domain Logic:** Implement `Notification.MarkDelivered(channel)` with idempotency checks (cannot mark delivered if already delivered).

---

## Phase 2: Robust Saga & Failure Handling

**Objective:** Implement reliable multi-service coordination with explicit failure paths and compensating transactions, moving beyond the "happy path" implementation.

### 2.1. Complete the Trip Creation Saga (Choreography)
*   **Implement `Ride.MatchFailed` Path:**
    *   **Dispatch Service:** If no drivers are found after the waterfall search, publish a `Trip.MatchFailed` event (or `DispatchError.NoDriversAvailable`).
    *   **Ride Service:** Implement a consumer for `Trip.MatchFailed`. Upon receipt, update `Trip.Status` to `CANCELLED` and set `CancellationReason`.
    *   **Notification Service:** Consume `Trip.MatchFailed` to notify the passenger: "Sorry, no drivers are available in your area."
*   **Event Consistency:** Ensure all services use the same `ride_events` stream and follow the naming conventions in `04-data-management-async-patterns.md`.

### 2.2. Distributed Timeout Detection (Compensating Transactions)
*   **Ride Service "Stuck" Trip Detector:**
    *   Implement a background worker in the `Ride Service` that periodically polls for trips in `REQUESTED` state older than 60 seconds.
    *   **Compensating Action:** Automatically transition these trips to `REQUEST_TIMEOUT` and publish a `Ride.Timeout` event to clean up any potential partial state in `Dispatch`.

### 2.3. Outbox Worker Hardening
*   **Status Tracking:** Update the `OutboxWorker` to handle `FAILED` states and `RetryCount` logic.
*   **Reliability:** Ensure that if the Redis Stream is down, the Outbox worker keeps the event in `UNPUBLISHED` status and retries with exponential backoff.

### 2.4. Error Event Standardization
*   Define a clear set of error events (e.g., `DispatchError.DriverOccupied`, `DispatchError.InvalidTripState`) to allow downstream services to react gracefully to business failures.

---

## Phase 3: Idempotency & Event Reliability

**Objective:** Ensure the system is "Self-Healing" by implementing robust idempotency guards that handle at-least-once delivery and event replays safely.

### 3.1. Consumer-Level Idempotency (Deduplication)
*   **Unique Event Tracking:**
    *   **Notification Service:** Implement an `idempotency_key` unique constraint in the `notifications` table using the Redis Stream Message ID. Use `ON CONFLICT DO NOTHING` to prevent duplicate notifications from being sent on message redelivery.
    *   **Dispatch Service:** Track `processed_trip_ids` in Redis or DB to ensure a trip is only matched once, even if the `Ride.Requested` event is re-read.
*   **Proper ACK Management:** Update all workers to only call `XACK` **after** the local transaction (DB write) has successfully committed.

### 3.2. Business-Level Idempotency (State Guards)
*   **Invariant Protection:**
    *   In the `Trip` domain model, ensure methods like `AcceptByDriver` check the current status. If a trip is already `ACCEPTED` or `CANCELLED`, the method should return a "No-Op" success or a specific "Already Processed" error instead of overwriting state.
*   **Versioning / Optimistic Locking:**
    *   Add an `updated_at` or `version` column to the `trips` table. Use this in `UPDATE` queries to ensure that a stale worker doesn't overwrite a more recent status change.

### 3.3. Event Replay Safety
*   **Side-Effect Isolation:** Ensure that side effects like WebSocket pushes are designed to be safe if repeated (e.g., the frontend handles duplicate "Driver Matched" messages gracefully by checking the current local state).

---

## Phase 4: Observability & Production Readiness

**Objective:** Prepare the services for deployment into the K3s cluster by implementing standard observability patterns and lifecycle management.

### 4.1. Distributed Tracing (Correlation IDs)
*   **Traceability:**
    *   Implement a middleware to extract or generate a `X-Correlation-ID` for every incoming HTTP request.
    *   **Propagate to Events:** Ensure this `correlation_id` is included in all `OutboxEvent` payloads and Redis Stream messages.
    *   **Propagate to Logs:** Include the `correlation_id` in every log line to allow tracing a single ride request across all four microservices.

### 4.2. Health & Lifecycle Management
*   **Kubernetes Probes:**
    *   Implement `/healthz` (Liveness) and `/readyz` (Readiness) endpoints for every service.
    *   The Readiness probe should verify connectivity to dependent resources (PostgreSQL, Redis).
*   **Graceful Shutdown:**
    *   Update `main.go` in all services to listen for `SIGTERM` and `SIGINT`.
    *   Ensure the service stops accepting new requests, finishes processing current outbox events/messages, and closes DB/Redis connections before exiting.

### 4.3. Structured Logging & Metrics
*   **JSON Logging:** Switch all services to structured JSON logging (using `slog` or `zap`) to facilitate log aggregation in the K8s cluster (EFK/Loki).
*   **Basic Metrics:** Expose a `/metrics` endpoint for Prometheus to track request latency, error rates, and outbox backlog size.

### 4.4. Configuration Management
*   **Environment Parity:** Ensure all services use a consistent environment variable naming convention (e.g., `DB_URL`, `REDIS_ADDR`, `JWT_SECRET`) that can be easily mapped to Kubernetes ConfigMaps and Sealed Secrets.

---

**Saga Hardening Plan Complete.** This document now serves as the master checklist for finalizing the application logic before we begin the Phase 4 Infrastructure setup (Vagrant/K3s).

**Confirmation Required:** Does this Phase 3 plan for Idempotency cover your reliability concerns? If confirmed, I will add the final Phase 4: Observability & Production Readiness.

**Confirmation Required:** Does this Phase 2 plan for Saga robustness meet your requirements? If confirmed, I will proceed to Phase 3: Idempotency & Event Reliability.

**Confirmation Required:** Does this detailed Phase 1 plan cover the DDD alignment you expect? If so, I will proceed to Phase 2: Robust Saga & Failure Handling.
