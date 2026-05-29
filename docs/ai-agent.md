# AI Incident Response Agent

> Status: planned (Plan 10). Manifests in vroom-gitops `platform/agents/`.

## What it does

When AlertManager fires an alert, an automated ReAct agent:

1. **Collects evidence** — queries Prometheus metrics and Loki error logs for the affected service
2. **Retrieves runbook** — `runbook-retriever` finds the most relevant runbook paragraphs via keyword search
3. **Diagnoses** — calls an OpenRouter LLM with a focused prompt: evidence + runbook → `{diagnosis, command, confidence}`
4. **Posts to Slack** — message includes an approval link (`http://192.168.25.133/n8n/webhook/approve`)
5. **Executes** — operator clicks the link (same LAN); `kubectl-executor` runs the approved command from an allowlist
6. **Reports result** — execution output posted back to Slack

**Orchestrator:** n8n (self-hosted on K3s) runs the full workflow. The LLM answers one question per step — it never holds state or calls tools directly.

---

## Components

### `kubectl-executor/`
Python Flask service. Accepts `POST /execute` with `{"command": "kubectl ..."}`. Validates every command against a regex allowlist before running. Requires `EXECUTOR_TOKEN` bearer token.

Allowed commands (examples):
- `kubectl get pods [-n <namespace>]`
- `kubectl logs <pod> -n <namespace> [--tail=N]`
- `kubectl describe pod <pod> -n <namespace>`
- `kubectl rollout status deployment/<name> -n <namespace>`

### `runbook-retriever/`
Python Flask service. Loads all `.md` files from `DOCS_DIR` (default `/docs`) at startup, splits them into paragraphs, and scores against a query string using keyword overlap. Returns the top-3 matching paragraphs.

`GET /retrieve?q=<alert-name>` → `[{source, text, score}]`

### `runbooks/`
Markdown runbooks. One file per alert type. The retriever reads these at runtime — add a new `.md` file to extend coverage without redeploying.

---

## Approval gate

No public URL or Slack app interactive components needed. The Slack message contains:
```
http://192.168.25.133/n8n/webhook/approve?token=<one-time-token>
```
The operator (on the same LAN as the cluster) clicks the link. n8n's Wait node resumes execution.

---

## Token cost profile

Each incident invocation uses ~1 LLM call (diagnosis step). The evidence bundle is kept under 2,000 tokens via Prometheus query windowing and Loki line limits.
