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
curl -s -X POST http://localhost:5002/investigate \
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
