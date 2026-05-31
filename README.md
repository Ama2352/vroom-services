# vroom-services

Go microservices backend, React frontend, and CI/CD pipeline for the **Vroom** ride-hailing platform.

Part of a three-repo GitOps setup — each repo has a single responsibility:

| Repo | Responsibility |
|------|---------------|
| **vroom-services** (this repo) | Application source code, Dockerfiles, GitLab CI pipeline |
| [vroom-gitops](https://github.com/Ama2352/vroom-gitops) | Kustomize manifests, ArgoCD Applications, Kargo promotion CRDs |
| [vroom-infra](https://github.com/Ama2352/vroom-infra) | Vagrant + Ansible K3s cluster provisioning |

---

## Architecture at a Glance

Four Go microservices communicate through **Redis Streams** using the **Outbox pattern** to guarantee delivery. Driver matching is a **Saga choreography** — no central orchestrator, compensating transactions handle failures.

```
Passenger app                    Driver app
     │                                │
     ▼                                ▼
[user-service] ──JWT─► [ride-service] ──outbox──► Redis Stream: ride_events
                                                        │              │
                                              [dispatch-service]  [notification-service]
                                              Redis Geo matching    WebSocket push
                                              Saga coordinator
```

### Applied patterns

| Pattern | Where | Why |
|---------|-------|-----|
| **Domain-Driven Design** | Each service's `internal/domain/` | Trip state machine + value objects own the business rules |
| **Transactional Outbox** | `ride-service` → Redis Streams | Prevents dual-write: event is committed atomically with the trip row |
| **Saga Choreography** | `ride` ↔ `dispatch` via Redis Streams | No orchestrator process; each service reacts to events and compensates on failure |
| **Consumer Groups** | `dispatch_group`, `notification_group` on `ride_events` | At-least-once delivery with XAUTOCLAIM for crash recovery |
| **Repository pattern** | `internal/repository/` in each service | Isolates DB access; SQLC generates the implementation |
| **JWT RS256** | `user-service` issues; others validate via `JWT_PUBLIC_KEY_PEM` | Asymmetric — only user-service holds the private key |
| **Redis Geo** | `dispatch-service`: `drivers:available` | O(log N) radius search; 5 km waterfall to nearest driver |

Full pattern details: [docs/architecture.md](docs/architecture.md)

---

## Repository Layout

```
vroom-services/
├── services/                   Application code
│   ├── user/                   Identity — JWT RS256, user CRUD
│   │   ├── internal/
│   │   │   ├── domain/         User entity, value objects
│   │   │   ├── handler/        Gin HTTP handlers
│   │   │   ├── repository/     DB interface + SQLC postgres impl
│   │   │   └── service/        Business logic
│   │   ├── migrations/         golang-migrate SQL files
│   │   ├── sqlc.yaml           SQLC config
│   │   └── Dockerfile.dev      Alpine + Air hot-reload
│   ├── ride/                   Trip lifecycle — Outbox publisher, Saga participant
│   │   └── internal/
│   │       ├── domain/         Trip entity + state machine (REQUESTED→COMPLETED)
│   │       ├── worker/         OutboxWorker (polls → XADD), TripTimeoutWorker
│   │       └── integration/    testcontainers integration tests
│   ├── dispatch/               Driver matching — Saga coordinator, Redis Geo
│   │   └── internal/
│   │       ├── domain/         DriverState (AVAILABLE / ON_OFFER / ON_TRIP)
│   │       └── worker/         Redis Streams XReadGroup consumer
│   ├── notification/           Event fan-out — WebSocket push to clients
│   └── frontend/               React 19 + Vite (passenger + driver UIs)
├── ai-agent/                   ReAct incident response agent (Plan 10 — in progress)
│   ├── kubectl-executor/       Python — allowlist-gated kubectl HTTP gateway
│   ├── runbook-retriever/      Python — keyword-search RAG over runbooks
│   └── runbooks/               Operational runbooks (Markdown)
├── load-tests/                 k6 scenarios
│   ├── baseline.js             50 VU / 2 min — P95 < 500 ms
│   ├── spike.js                Ramp to 200 VU
│   └── geo_flood.js            200 drivers × 2 s — P95 < 50 ms (dispatch stress)
├── scripts/
│   └── init-db.sql             Bootstrap DB users + schemas for local dev
└── docker-compose.yml          Full local stack (Postgres + Redis + all services + frontend)
```

Each Go service follows the same internal layout. See [docs/architecture.md](docs/architecture.md) for the canonical structure.

---

## Quick Start (local, no Kubernetes needed)

```bash
# Full stack with hot reload
docker-compose up --build

# User:         http://localhost:8081
# Ride:         http://localhost:8082
# Dispatch:     http://localhost:8083
# Notification: http://localhost:8084
# Frontend:     http://localhost:5173
```

```bash
# Single service (fastest iteration)
docker-compose up postgres redis -d
cd services/ride
PORT=8082 go run ./...
```

```bash
# Tests
cd services/ride
go test ./... -v
go test ./internal/integration/... -tags integration -v   # requires Docker
```

---

## CI/CD Pipeline (GitLab CI)

```
test → integration → build → publish → deploy
```

| Stage | What runs | Notes |
|-------|-----------|-------|
| `test` | `go test ./...` per service | gosec SAST + GitLab SAST runs here |
| `integration` | testcontainers (real Postgres + Redis) | `//go:build integration` tag; ride + dispatch only |
| `build` | Docker multi-stage build → `.tar` artifact | `BASE_IMAGE_PREFIX=` keeps images on Docker Hub |
| `publish` | Push to GHCR (`ghcr.io/ama2352/vroom-mvp-*`) | Tags: `latest`, semver, short SHA |
| `deploy` | Patch image tag in vroom-gitops dev overlay | ArgoCD syncs → Kargo promotes dev→staging→prod |

Required CI variables (GitLab Settings → CI/CD → Variables):

| Variable | Purpose |
|----------|---------|
| `GHCR_USER` | GitHub username |
| `GHCR_TOKEN` | GitHub PAT with `write:packages` scope |
| `GITHUB_GITOPS_TOKEN` | Classic PAT with `repo` scope — pushes overlay changes to vroom-gitops |

---

## Documentation

- [Architecture & patterns](docs/architecture.md) — DDD layers, Outbox flow, Saga steps, state machine, auth, driver geo
- [API reference](docs/api.md) — all endpoints across all 4 services
- [AI agent](docs/ai-agent.md) — ReAct incident responder (kubectl-executor + runbook-retriever)
