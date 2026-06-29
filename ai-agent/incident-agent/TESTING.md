# Incident Agent — Manual Testing Guide

Two modes: **Mock** (zero API calls, instant) and **Real Groq** (live LLM).
Use Mock for iterating on workflow/logic. Use Real Groq only to validate LLM reasoning.

---

## Quick Reference

| What | Value |
|---|---|
| Agent URL (port-forward) | `http://localhost:5002` |
| n8n URL | `http://192.168.25.139:30078/` |
| Toggle mock mode | `kubectl set env deployment/incident-agent -n monitoring LLM_MOCK=true` |
| Toggle real mode | `kubectl set env deployment/incident-agent -n monitoring LLM_MOCK=false` |
| Change scenario | `kubectl set env deployment/incident-agent -n monitoring LLM_MOCK_SCENARIO=crashloop` |

Port-forward if NodePort is unreachable:
```bash
kubectl port-forward -n monitoring svc/incident-agent 5002:5002 &
# Then use http://localhost:5002
```

---

## Reset Before Each Test Run

Run these before starting any scenario to ensure a clean slate:

```bash
# 1. Clear episodic memory (incidents stored from previous runs)
curl -s -X POST http://localhost:5002/admin/reset-incidents | python3 -m json.tool
# Expected: {"cleared": N}

# 2. Re-seed runbook from vroom-ops.md (restores bootstrap entries, removes learned)
curl -s -X POST http://localhost:5002/admin/reseed | python3 -m json.tool
# Expected: {"seeded": 5}

# 3. Re-enable mock mode (resets after every pod restart / image deploy)
#    For Scenario A (scale to zero):
kubectl set env deployment/incident-agent -n monitoring \
  LLM_MOCK=true \
  LLM_MOCK_SCENARIO=scale_to_zero
#    For Scenario B (crashloop) — change scenario BEFORE injecting failure:
# kubectl set env deployment/incident-agent -n monitoring \
#   LLM_MOCK=true \
#   LLM_MOCK_SCENARIO=crashloop

# 4. Restart port-forward if the pod restarted
pkill -f "port-forward.*5002" 2>/dev/null; sleep 2
kubectl port-forward -n monitoring svc/incident-agent 5002:5002 &
sleep 2

# 5. Restore cluster state (in case a previous scenario left failures injected)
kubectl scale deployment/ride-service -n vroom-dev --replicas=1
# Note: trailing '-' is kubectl syntax to REMOVE the env var (unset REDIS_ADDR)
kubectl set env deployment/ride-service -n vroom-dev REDIS_ADDR-
```

---

## Flow 1 — Mock Mode (recommended for dev iteration)

No API key required. Full pipeline runs: n8n → agent → kubectl tools → memory → Slack.

### Step 1: Enable mock mode

```bash
kubectl set env deployment/incident-agent -n monitoring \
  LLM_MOCK=true \
  LLM_MOCK_SCENARIO=scale_to_zero
```

Verify the pod restarted:
```bash
kubectl rollout status deployment/incident-agent -n monitoring
```

### Step 2: Inject the failure

**Scenario A — Scale to zero:**
```bash
kubectl scale deployment/ride-service -n vroom-dev --replicas=0
# Verify: no pods
kubectl get pods -n vroom-dev -l app=ride-service
```

**Scenario B — Crashloop:**
```bash
# Switch agent scenario FIRST
kubectl set env deployment/incident-agent -n monitoring LLM_MOCK_SCENARIO=crashloop

# Inject failure
kubectl set env deployment/ride-service -n vroom-dev REDIS_ADDR=bad-host:6379
# ride service pings Redis at startup (main.go:72) — exits immediately on failure
# Wait ~30s for CrashLoopBackOff
kubectl get pods -n vroom-dev -l app=ride-service
```

### Step 3: Trigger the agent directly (bypass n8n)

```bash
curl -s -X POST http://localhost:5002/investigate?debug=true \
  -H "Content-Type: application/json" \
  -d '{
    "alert_name": "HighErrorRate",
    "service":    "ride-service",
    "namespace":  "vroom-dev"
  }' | python3 -m json.tool
```

Expected response:
```json
{
  "confidence": "HIGH",
  "remediation": {
    "tool": "scale_deployment",
    "args": { "deployment": "ride-service", "namespace": "vroom-dev" }
  },
  "rewoo_steps": 2,
  "suggested_command": "kubectl scale deployment/ride-service -n vroom-dev --replicas=1"
}
```

Save `execution_id` from the response:
```bash
EXEC_ID="<paste execution_id here>"
```

### Step 4: Approve remediation

```bash
curl -s -X POST http://localhost:5002/remediate \
  -H "Content-Type: application/json" \
  -d "{\"execution_id\": \"$EXEC_ID\", \"approved\": true}" | python3 -m json.tool
```

Expected: `"outcome": "resolved"`

### Step 5: Verify memory learning

After remediation, the mock reflection thread stores a learned runbook entry:

```bash
curl -s http://localhost:5002/admin/runbook
```

Expected: markdown with a new `(learned)` entry for the incident.

### Step 6: Verify memory was stored

After Step 4 remediation, the reflect thread writes a learned runbook entry. Verify:

```bash
# 1. Confirm learned entry exists in runbook
curl -s http://localhost:5002/admin/runbook | grep -A5 "learned"

# 2. Confirm memory search returns the stored incident
curl -s "http://localhost:5002/memory/search?q=HighErrorRate+ride-service"

# 3. Check logs for reflect confirmation line
kubectl logs -n monitoring -l app=incident-agent --tail=30 | grep "reflect"
# Expected: [reflect] mock stored: Mock: HighErrorRate on ride-service
```

Run a second trigger — the Planner now has memory context:
```bash
curl -s -X POST http://localhost:5002/investigate \
  -H "Content-Type: application/json" \
  -d '{"alert_name":"HighErrorRate","service":"ride-service","namespace":"vroom-dev"}' \
  | python3 -m json.tool
# confidence should remain HIGH; memory_context was injected but mock LLM ignores it
```

### Step 7: Restore the cluster

```bash
# Restore scale_to_zero scenario:
kubectl scale deployment/ride-service -n vroom-dev --replicas=1

# Restore crashloop scenario:
kubectl set env deployment/ride-service -n vroom-dev REDIS_ADDR-
```

---

## Test 2 — CrashLoop via Bad REDIS_ADDR (Mock)

Tests Scenario B end-to-end: bad Redis address → ride-service CrashLoopBackOff → agent detects crash logs → recommends `restart_deployment`.

### Step 1: Reset and set crashloop scenario

```bash
curl -s -X POST http://localhost:5002/admin/reset-incidents | python3 -m json.tool
curl -s -X POST http://localhost:5002/admin/reseed | python3 -m json.tool

kubectl set env deployment/incident-agent -n monitoring \
  LLM_MOCK=true \
  LLM_MOCK_SCENARIO=crashloop

kubectl rollout status deployment/incident-agent -n monitoring
```

### Step 2: Inject the failure

```bash
kubectl set env deployment/ride-service -n vroom-dev REDIS_ADDR=bad-host:6379
# ride-service pings Redis at startup (main.go:72) — exits immediately on failure
```

Wait ~30 s for CrashLoopBackOff to appear:
```bash
kubectl get pods -n vroom-dev -l app=ride-service
# Expected: STATUS=CrashLoopBackOff, RESTARTS≥1
```

### Step 3: Trigger the agent

```bash
curl -s -X POST "http://localhost:5002/investigate?debug=true" \
  -H "Content-Type: application/json" \
  -d '{
    "alert_name": "PodCrashLooping",
    "service":    "ride-service",
    "namespace":  "vroom-dev"
  }' | python3 -m json.tool
```

Expected response:
```json
{
  "confidence": "HIGH",
  "remediation": {
    "tool": "restart_deployment",
    "args": { "deployment": "ride-service", "namespace": "vroom-dev" }
  },
  "rewoo_steps": 2,
  "suggested_command": "kubectl rollout restart deployment/ride-service -n vroom-dev"
}
```

Save `execution_id`:
```bash
EXEC_ID="<paste execution_id here>"
```

### Step 4: Approve remediation

```bash
curl -s -X POST http://localhost:5002/remediate \
  -H "Content-Type: application/json" \
  -d "{\"execution_id\": \"$EXEC_ID\", \"approved\": true}" | python3 -m json.tool
```

Expected: `"outcome": "resolved"`

> **Note:** `restart_deployment` rolls the pod but the bad env var is still set, so the pod will crash again immediately. That is expected — the agent's job is detection + recommendation, not fixing the root cause. Restore in Step 6.

### Step 5: Verify memory learning

```bash
# Confirm learned runbook entry was written
curl -s http://localhost:5002/admin/runbook | grep -A5 "learned"

# Confirm memory search returns the incident
curl -s "http://localhost:5002/memory/search?q=PodCrashLooping+ride-service"

# Check logs for reflect line
kubectl logs -n monitoring -l app=incident-agent --tail=30 | grep "reflect"
# Expected: [reflect] mock stored: Mock: PodCrashLooping on ride-service
```

### Step 6: Restore the cluster

```bash
# Remove the bad env var (trailing '-' unsets it)
kubectl set env deployment/ride-service -n vroom-dev REDIS_ADDR-

# Wait for ride-service to come back healthy
kubectl rollout status deployment/ride-service -n vroom-dev
kubectl get pods -n vroom-dev -l app=ride-service
```

---

## Flow 2 — Via n8n Workflow (end-to-end)

### Step 1: Enable mock mode (same as Flow 1, Step 1)

### Step 2: Open n8n and locate the Vroom incident workflow

Go to `http://192.168.25.139:30078/` → Workflows → find "Vroom Incident Response" (or similar).

### Step 3: Inject failure and manually trigger the webhook

```bash
# Inject failure first
kubectl scale deployment/ride-service -n vroom-dev --replicas=0

# Then send alert to n8n webhook
curl -s -X POST http://192.168.242.10:30078/webhook-test/vroom-alert \
  -H "Content-Type: application/json" \
  -d '{
    "alerts": [{
      "labels": {
        "alertname": "HighErrorRate",
        "service":   "ride-service",
        "namespace": "vroom-dev",
        "severity":  "critical"
      },
      "status": "firing"
    }]
  }'
```

### Step 4: Approve in n8n

Watch the workflow execution in n8n UI. When it reaches the "Human Approval" node, click Approve.

### Step 5: Verify in Slack (if AlertManager wired)

Check Slack channel for the remediation notification.

---

## Flow 2b — n8n End-to-End: CrashLoop via Bad REDIS_ADDR

Full path: inject bad Redis addr → AlertManager-style webhook → n8n workflow → incident-agent → human approval → `restart_deployment` via kubectl-executor → Slack notification.

### Step 1: Reset and set crashloop scenario

```bash
curl -s -X POST http://localhost:5002/admin/reset-incidents | python3 -m json.tool
curl -s -X POST http://localhost:5002/admin/reseed | python3 -m json.tool

kubectl set env deployment/incident-agent -n monitoring \
  LLM_MOCK=true \
  LLM_MOCK_SCENARIO=crashloop

kubectl rollout status deployment/incident-agent -n monitoring
```

### Step 2: Open n8n and locate the workflow

Go to `http://192.168.25.139:30078/` → Workflows → open "Vroom Incident Response".

Switch the webhook node to **Test mode** (click "Listen for test event") so the next curl triggers a live execution you can watch step by step.

### Step 3: Inject the failure

```bash
kubectl set env deployment/ride-service -n vroom-dev REDIS_ADDR=bad-host:6379
```

Wait ~30 s for CrashLoopBackOff:
```bash
kubectl get pods -n vroom-dev -l app=ride-service
# Expected: STATUS=CrashLoopBackOff, RESTARTS≥1
```

### Step 4: Send the alert webhook to n8n

```bash
curl -s -X POST http://192.168.242.10:30078/webhook-test/vroom-alert \
  -H "Content-Type: application/json" \
  -d '{
    "alerts": [{
      "labels": {
        "alertname": "PodCrashLooping",
        "service":   "ride-service",
        "namespace": "vroom-dev",
        "severity":  "critical"
      },
      "status": "firing"
    }]
  }'
```

> Use `webhook-test/` (test path) while the webhook node is in Listen mode. Switch to `webhook/` for production runs.

### Step 5: Watch the workflow in n8n UI

Track each node as it executes:

| Node | What to check |
|---|---|
| Webhook | Payload received — `alertname=PodCrashLooping`, `namespace=vroom-dev` |
| HTTP Request (investigate) | POST to `http://incident-agent.monitoring.svc.cluster.local:5002/investigate` |
| Parse + Build Approval | `rewoo_steps` present; `suggested_command` = `kubectl rollout restart ...` |
| Human Approval | Wait here — do **not** click yet; verify the data looks correct first |

Check agent logs while waiting:
```bash
kubectl logs -n monitoring deployment/incident-agent -f | grep -E "^\[(rewoo|reflect|memory|remediate|seed)"
# Expected lines:
# [memory] pre-fetch: incidents=0 runbook=N ...
# [rewoo] alert=PodCrashLooping service=ride-service mock=True
# [rewoo:planner] mock=True scenario=crashloop
# [rewoo] plan=[('get_pods', ...), ('get_logs', ...)]
# [rewoo:worker] E1 action=get_pods ...
# [rewoo:worker] E2 action=get_logs ...
# [rewoo:solver] mock=True scenario=crashloop
```

### Step 6: Approve in n8n

Click **Approve** on the Human Approval node.

The next node (HTTP Request to kubectl-executor) sends:
```
kubectl rollout restart deployment/ride-service -n vroom-dev
```

Expected agent log after approval:
```
[remediate] dispatching: kubectl rollout restart deployment/ride-service -n vroom-dev
[remediate] waiting 35s for pod restart...
[remediate] post-restart: ... → outcome=resolved
[reflect] mock stored: Mock: PodCrashLooping on ride-service
```

### Step 7: Verify Slack notification (if wired)

Check the Slack channel for a message containing:
- Service: `ride-service`
- Alert: `PodCrashLooping`
- Action taken: `restart_deployment`
- Outcome: `resolved`

If Slack is not yet wired, confirm the final n8n node shows `outcome=resolved` in its output data.

### Step 8: Restore the cluster

```bash
# Remove the bad env var
kubectl set env deployment/ride-service -n vroom-dev REDIS_ADDR-

# Confirm ride-service recovers
kubectl rollout status deployment/ride-service -n vroom-dev
kubectl get pods -n vroom-dev -l app=ride-service
# Expected: STATUS=Running, RESTARTS stable
```

---

## Test 3 — DB Down: Pod Up + 5xx + Traces (Real Cluster)

Tests Scenario C end-to-end: PostgreSQL scaled to zero → ride-service stays up but returns 500 on every DB write → OTEL records error spans → `traces_errored > 0` in the evidence bundle → planner includes `get_traces` → enriched span output shows cross-service error attribution.

### Step 1: Reset and verify baseline

```bash
curl -s -X POST http://localhost:5002/admin/reset-incidents | python3 -m json.tool
curl -s -X POST http://localhost:5002/admin/reseed | python3 -m json.tool

# Confirm all services are healthy before injecting
kubectl get pods -n vroom-dev
kubectl get pods -n platform
```

### Step 2: Disable mock mode (real LLM required for this scenario)

```bash
kubectl set env deployment/incident-agent -n monitoring LLM_MOCK=false
kubectl rollout status deployment/incident-agent -n monitoring
```

### Step 3: Inject the failure — scale PostgreSQL to zero

```bash
# Verify the StatefulSet name first
kubectl get statefulset -n platform

# Scale down (substitute the correct name if different)
kubectl scale statefulset/postgresql -n platform --replicas=0

# Confirm DB pod is gone
kubectl get pods -n platform -l app=postgresql
# Expected: No resources found
```

### Step 4: Send ride requests to generate error spans

Get a valid passenger JWT (reuse one from a prior test session or call `POST /v1/auth/login` on user-service at port 8081):

```bash
TOKEN="<passenger jwt>"
for i in $(seq 1 5); do
  curl -s -X POST http://localhost:8082/v1/trips \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"pickup_lat":10.77,"pickup_lng":106.69,"dropoff_lat":10.78,"dropoff_lng":106.70}'
  sleep 1
done
```

Wait 20 seconds for Tempo to index the error spans:
```bash
sleep 20
```

### Step 5: Trigger the agent

```bash
curl -s -X POST "http://localhost:5002/investigate?debug=true" \
  -H "Content-Type: application/json" \
  -d '{
    "alert_name": "HighErrorRate",
    "service":    "ride-service",
    "namespace":  "vroom-dev"
  }' | python3 -m json.tool
```

Expected response (key fields):
```json
{
  "confidence":  "HIGH",
  "remediation": null,
  "dev_hint":    "Dev action: PostgreSQL is unreachable ...\nkubectl: kubectl get pods -n platform ...",
  "debug": {
    "bundle": "... traces_errored=5 ..."
  }
}
```

Verify planner included `get_traces`:
```bash
kubectl logs -n monitoring deployment/incident-agent --tail=40 | grep -E "^\[(rewoo|memory)"
# Expected: [rewoo] plan=[ ..., ('get_traces', ...) ... ]
```

Verify enriched trace output in worker logs:
```bash
kubectl logs -n monitoring deployment/incident-agent --tail=40 | grep "rewoo:worker"
# Expected: [rewoo:worker] E3 obs=trace_id=... root=POST /v1/trips ...
#                                   error span: ride-service → ...
#                                   error: "..."
```

### Step 6: Restore the cluster

```bash
kubectl scale statefulset/postgresql -n platform --replicas=1
kubectl rollout status statefulset/postgresql -n platform
kubectl get pods -n platform -l app=postgresql
# Expected: STATUS=Running
```

Re-enable mock mode for subsequent tests:
```bash
kubectl set env deployment/incident-agent -n monitoring \
  LLM_MOCK=true \
  LLM_MOCK_SCENARIO=scale_to_zero
```

---

## Flow 3 — Real Groq (LLM quality validation)

Use this flow when changing prompts or switching models. Costs ~3 API calls per run.

### Step 1: Confirm Groq secret is sealed and applied

```bash
kubectl get secret groq-secret -n monitoring
# Expected: NAME=groq-secret, TYPE=Opaque
```

If missing, seal it first:
```bash
# On your machine: add to vroom-infra/ansible/vars/secrets.yml
#   groq_api_key: "gsk_..."
# Then:
vagrant provision k3s-server --provision-with seal-secrets.yml
```

### Step 2: Disable mock mode

```bash
kubectl set env deployment/incident-agent -n monitoring LLM_MOCK=false
kubectl rollout status deployment/incident-agent -n monitoring
```

### Step 3: Run the same inject + curl as Flow 1, Steps 2–4

Watch the logs to see real LLM responses:
```bash
kubectl logs -n monitoring -l app=incident-agent -f | grep -E "rewoo|planner|solver|reflect"
```

Expected log pattern:
```
[rewoo] alert=HighErrorRate service=ride-service mock=False
[rewoo] plan=[('get_pods', {...}), ('get_events', {...})]
[rewoo] E1 get_pods obs=No resources found...
[rewoo] E2 get_events obs=ScalingReplicaSet...
[reflect] stored: Deployment scaled to zero on ride-service
```

### Step 4: Hot-swap model (optional)

```bash
# Switch to faster Groq model
curl -s -X POST http://localhost:5002/admin/models \
  -H "Content-Type: application/json" \
  -d '[
    {"id": "llama-3.1-8b-instant", "provider": "groq"},
    {"id": "llama-3.3-70b-versatile", "provider": "groq"}
  ]'

# Verify
curl -s http://localhost:5002/admin/models
```

---

## Useful Debug Commands

```bash
# Live agent logs
kubectl logs -n monitoring -l app=incident-agent -f

# Check current env vars (including LLM_MOCK)
kubectl exec -n monitoring deploy/incident-agent -- env | grep -E "LLM|GROQ|OPENROUTER"

# Check Redis memory directly
kubectl exec -n platform deploy/redis -- redis-cli SCARD incidents:index
kubectl exec -n platform deploy/redis -- redis-cli SCARD runbook:index

# View runbook entries
curl -s http://localhost:5002/admin/runbook

# Search memory
curl -s "http://localhost:5002/memory/search?q=HighErrorRate+ride-service"

# Reset runbook (clears learned + reseeds from vroom-ops.md)
curl -s -X POST http://localhost:5002/admin/reseed

# Check current model config
curl -s http://localhost:5002/admin/models | python3 -m json.tool
```

---

## Expected Test Matrix

| Scenario | Mock | Tool called | Expected remediation_tool |
|---|---|---|---|
| Scale to zero | ✓ | get_pods, get_events | `scale_deployment` |
| Crashloop | ✓ | get_pods, get_logs | `restart_deployment` |
| Healthy service | ✓ | get_pods | `none` (LOW confidence) |
| Scale to zero | ✗ (Groq) | get_pods, get_events | `scale_deployment` |
| Second run (memory) | ✓ | get_pods | `scale_deployment` + confidence HIGH |

---

## Troubleshooting

**Agent pod keeps restarting:**
```bash
kubectl describe pod -n monitoring -l app=incident-agent | tail -20
kubectl logs -n monitoring -l app=incident-agent --previous
```

**`curl` returns connection refused:**
```bash
# Check pod is running
kubectl get pods -n monitoring -l app=incident-agent
# Use port-forward instead of NodePort
kubectl port-forward -n monitoring svc/incident-agent 5002:5002 &
curl http://localhost:5002/health
```

**`LLM_MOCK=true` still seems to call OpenRouter (check logs):**
```bash
# Confirm env var took effect after rollout
kubectl exec -n monitoring deploy/incident-agent -- env | grep LLM_MOCK
# Expected: LLM_MOCK=true
```

**`groq-secret` missing after sealing:**
```bash
# Check the secret exists
kubectl get sealedsecret groq-secret -n monitoring
# If SealedSecret exists but Secret doesn't — Sealed Secrets controller issue:
kubectl logs -n kube-system -l app.kubernetes.io/name=sealed-secrets --tail=20
```
