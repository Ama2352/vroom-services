# vroom-services

Source code, CI/CD pipeline, and AI agent tooling for the **Vroom** ride-hailing platform.

Part of a three-repo GitOps setup:
- **vroom-services** (this repo) — application source + CI
- [vroom-gitops](https://github.com/Ama2352/vroom-gitops) — Kustomize overlays, ArgoCD + Kargo delivery
- [vroom-infra](https://github.com/Ama2352/vroom-infra) — Vagrant + Ansible K3s cluster provisioning

---

## Repository Layout

```
vroom-services/
├── user/               Go — identity, JWT RS256, PostgreSQL
├── ride/               Go — trip state machine, Outbox pattern, PostgreSQL + Redis Streams
├── dispatch/           Go — driver geo-matching, Saga coordinator, Redis Geo
├── notification/       Go — event consumer, WebSocket push, PostgreSQL
├── frontend/           React 19 + Vite — passenger and driver UI
├── ai-agent/           ReAct incident response agent (Plan 10)
│   ├── kubectl-executor/   Python — allowlist-gated kubectl HTTP gateway
│   ├── runbook-retriever/  Python — keyword-search RAG over runbooks
│   └── runbooks/           Operational runbooks (Markdown)
├── load-tests/         k6 load scenarios (baseline, spike, geo flood)
├── scripts/            DB init and utility scripts
└── docker-compose.yml  Full local stack (Postgres + Redis + all services)
```

---

## Quick Start (local, no Kubernetes needed)

```bash
docker-compose up --build
# User:         http://localhost:8081
# Ride:         http://localhost:8082
# Dispatch:     http://localhost:8083
# Notification: http://localhost:8084
# Frontend:     http://localhost:5173
```

---

## CI/CD Pipeline (GitLab CI)

```
test → integration → build → publish → deploy
```

| Stage | What happens |
|-------|-------------|
| `test` | `go test ./...` + gosec SAST |
| `integration` | testcontainers (real Postgres + Redis), `//go:build integration` |
| `build` | Docker multi-stage build |
| `publish` | Push to GHCR (`ghcr.io/ama2352/vroom-mvp-*`) |
| `deploy` | Update image tag in vroom-gitops dev overlay → ArgoCD syncs → Kargo promotes |

---

## Documentation

- [Architecture & patterns](docs/architecture.md)
- [API reference](docs/api.md)
- [AI agent (ReAct incident responder)](docs/ai-agent.md)
