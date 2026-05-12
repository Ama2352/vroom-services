---
name: vroom-monitor-report
description: Analyzes Vroom ride-hailing platform RED metrics, Loki error logs, and deployment
             context to generate health reports and Kargo promotion gate verdicts.
             Use this skill when asked to: analyze Prometheus metrics, check service health,
             generate a platform health summary, verify a Kargo deployment gate, detect
             post-deploy regressions, or surface Loki error log samples.
---

# Vroom Monitor: Health Analysis & Kargo Promotion Gate

## 1. Quick Start

Collect current metrics: `python skill/scripts/collect_metrics.py`  
Threshold definitions: `skill/references/thresholds.md`  
Regression definitions (verify mode): `skill/references/regression-thresholds.md`

Test all scenarios offline: `GEMINI_API_KEY=... python skill/scripts/test_scenarios.py`

---

## 2. Mandatory Reporting Rules

These rules MUST appear in every Gemini system instruction:

1. **ALWAYS** state the exact numeric `error_logs` count in every service finding, even if 0.
2. **NEVER** write "No errors" or "no error logs" when `error_logs > 0`.  
   Write: `"2 error logs (below WARN>10)"` or `"89 error logs (CRIT threshold exceeded)"`.
3. **ALWAYS** include `error_logs`, `error_rate_pct`, and `p99_latency_s` in every finding.
4. Status `OK` means within thresholds — **not** absence of issues. Always report actual values.

---

## 3. Token Budget

**Compact user prompt format** (not JSON — saves ~70% tokens):
```
ns={namespace} window={window}
svc|rps|err%|p99s|errlogs
user-service|9.1|0.0|0.31|0
ride-service|6.3|0.4|0.55|2

ride-service error samples (2):
  [10:45:22] ERROR pq: too many connections
  [10:45:19] ERROR context deadline exceeded
```

**max_output_tokens by mode:**
- Report mode: `1024`
- Verify mode: `2048`

**Pre-filter (report mode only):** skip Gemini entirely if all `error_logs=0` AND all metrics
within thresholds AND no pod restarts. Post `--healthy` and stop. Verify mode always calls Gemini.

---

## 4. Workflow — Report Mode (CronJob)

1. **Collect** → `python skill/scripts/collect_metrics.py`
   - Outputs `metrics.json` (includes `error_log_samples` if `error_logs > 0`)
   - Returns exit code 0 (within thresholds) or 1 (anomaly)
2. **Pre-filter** → if exit 0 AND no `error_logs` AND no pod issues: run `post_slack.py --healthy` and STOP. DO NOT call Gemini.
3. **Analyze** → `call_gemini(metrics)` using:
   - System: `thresholds.md` + MANDATORY RULES + two-scenario few-shot (`few-shot-example.json`)
   - Schema: `report-schema.json`
   - User prompt: compact table + log sample block
   - `max_output_tokens=1024`, `temperature=0.1`
4. **Validate** → `python skill/scripts/validate_report.py report.json`
   - FAIL: retry once with validation error as feedback
   - Second FAIL: `post_slack.py --unavailable` and abort
5. **Post** → `python skill/scripts/post_slack.py report.json`
   - WARN/CRIT services: log samples rendered as code blocks in Slack

---

## 5. Workflow — Verify Mode (Kargo Gates, `--mode=verify`)

1. **Wait** → sleep `STABILIZATION_WAIT_SECONDS` for pods to stabilize post-deploy
2. **Deploy context** → `python skill/scripts/collect_deploy_context.py`
   - Outputs `deploy_context.json` (image tags, rollout times, Kargo freight ID)
   - Non-fatal if fails (not running in-cluster) — reporter continues without it
3. **Baseline** → `python skill/scripts/collect_metrics.py --baseline`
   - Outputs `baseline_metrics.json` (pre-deploy window via `BASELINE_WINDOW` env var)
   - Non-fatal — reporter continues without baseline (confidence degrades to MEDIUM)
4. **Collect current** → `python skill/scripts/collect_metrics.py`
   - Always required; outputs `metrics.json`
5. **Analyze** → `call_gemini_verify(metrics, baseline, deploy_ctx)` using:
   - System: `thresholds.md` + `regression-thresholds.md` + MANDATORY RULES + RECOMMENDATION FORMAT RULES + verify few-shot (`few-shot-verify-example.json`)
   - Schema: `report-schema-verify.json` (extended: adds `regression_detected`, `deployment_risk`, `confidence`, `kargo_annotation`)
   - `max_output_tokens=2048`, `temperature=0.1`
6. **Validate** → `python skill/scripts/validate_report.py report.json` — same retry logic
7. **Post** → `python skill/scripts/post_slack.py report.json --verify`
   - Header answers: "PROMOTE TO {namespace}: YES | YES — WITH CAUTION | DO NOT PROMOTE"
   - Regression services show inline delta tags and log samples
   - FAIL: numbered "Actions required before promotion"
   - PASS + regression: "Monitor after promotion" bullet list
8. **Gate decision** → FAIL if `overall_status=CRIT` OR (`regression_detected=true` AND `deployment_risk=CRITICAL`)
9. **Write** `gate-verdict.json` with `{verdict, overall_status, regression_detected, confidence, deployment_risk, kargo_annotation}`

---

## 6. Recommendation Format Rules (verify mode system prompt)

```
- If overall_status=CRIT: frame as numbered "Actions required before promotion"
  with specific kubectl commands where applicable.
- If regression_detected=true and NOT CRIT: frame as "Monitor after promotion: watch for..."
- If PASS clean: write "Platform is healthy. Proceed with promotion."
```

---

## 7. References

| File | Purpose | When Loaded |
|------|---------|-------------|
| `references/thresholds.md` | WARN/CRIT thresholds | All calls (system instruction) |
| `references/regression-thresholds.md` | Regression triggers + gate matrix | Verify mode only |
| `examples/few-shot-example.json` | Two-scenario worked examples (compact format) | Report mode calls |
| `examples/few-shot-verify-example.json` | Verify mode regression scenario | Verify mode calls |
| `examples/report-schema.json` | Gemini response schema (base) | Report mode calls |
| `examples/report-schema-verify.json` | Gemini response schema (extended) | Verify mode calls |

---

## 8. Test Harness

**Offline tests** (no cluster required):
```bash
# Full test — 10 scenarios with real Gemini:
GEMINI_API_KEY=... python skill/scripts/test_scenarios.py

# Flow/format test — no API calls (validates pre-filter, exit codes, Slack format):
MOCK_GEMINI=1 python skill/scripts/test_scenarios.py

# Single scenario:
GEMINI_API_KEY=... python skill/scripts/test_scenarios.py --only 02_LOW_ERRORS
```

**Fixtures** in `skill/scripts/test_fixtures/`:
- `scenario_01_healthy.json` — all OK, 0 errors (pre-filter bypass)
- `scenario_02_low_errors.json` — error10 bug scenario (2 error_logs each)
- `scenario_03_warn_error_rate.json` — ride-service WARN
- `scenario_04_crit_error_rate.json` — ride-service CRIT (gate fail)
- `scenario_05_crit_latency.json` — p99 CRIT
- `scenario_06_warn_logs.json` — error_logs WARN
- `scenario_07_crit_logs.json` — error_logs CRIT
- `scenario_08_mixed.json` — one CRIT, rest OK
- `scenario_09_regression.json` + `scenario_09_regression_baseline.json` — regression detection
- `scenario_10_oom.json` — OOMKill (CRITICAL risk, gate fail)

**Cluster injection** (real Prometheus/Loki):
```bash
# Switch scenario-injector to generate error logs:
kubectl set env deployment/vroom-scenario-injector SCENARIO=error-logs -n vroom-dev

# Run reporter manually against the cluster:
kubectl run reporter-test --rm -it --image=ama2352/vroom-mvp-ai-reporter:latest \
  --env=GEMINI_API_KEY=... --env=TARGET_NAMESPACE=vroom-dev \
  --restart=Never -- python reporter.py --mode=report
```

---

## 9. Constraints

- `temperature=0.1` — factual, not creative
- `response_mime_type="application/json"` — always
- Secrets from environment variables only — never hardcode
- Do NOT call Gemini in report mode when pre-filter triggers (exit 0 AND no error_logs AND no pod issues)
- In verify mode: ALWAYS call Gemini — need gate verdict regardless of threshold state
- Bundled scripts handle their own exceptions — never let a script crash without a WARN message
