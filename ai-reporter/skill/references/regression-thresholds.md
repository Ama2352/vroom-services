# Regression Detection Thresholds

These thresholds trigger `regression_detected=true` even when absolute metrics remain below WARN levels.
Compare baseline window (pre-deploy) vs verify window (post-deploy).

| Metric | Regression Threshold | Deployment Risk |
|--------|---------------------|-----------------|
| error_logs | absolute delta ≥ 3 | MEDIUM |
| error_rate_pct | relative increase > 200% | HIGH |
| p99_latency_s | relative increase > 50% | MEDIUM |
| pod restart_count | any new restart > 0 | MEDIUM |
| pod oomkill_count | any OOMKill > 0 | CRITICAL |

## Gate Decision Matrix

| overall_status | regression_detected | deployment_risk | Gate Verdict |
|----------------|--------------------|--------------------|-------------|
| OK | false | any | PASS |
| OK | true | LOW/MEDIUM/HIGH | PASS (warn in annotation) |
| WARN | any | any | PASS (warn in annotation) |
| CRIT | any | any | FAIL |
| any | true | CRITICAL | FAIL |

## Confidence Levels

- **HIGH**: both baseline and current metrics are available
- **MEDIUM**: only current metrics available (baseline collection failed)
- **LOW**: partial data — some services missing instrumentation

## Notes

- Baseline collection is non-fatal. If it fails, set `confidence=MEDIUM` and omit regression analysis.
- `deployment_risk` must be the highest risk level triggered across all services.
- A single OOMKill on any service upgrades `deployment_risk` to CRITICAL regardless of other metrics.
