# Anomaly Thresholds

| Metric | WARN | CRIT |
|--------|------|------|
| error_rate_pct | > 1.0% | > 5.0% |
| p99_latency_s  | > 2.0s | > 5.0s |
| error_log_count (30 min window) | > 10 | > 50 |

A service is WARN if any single metric exceeds the WARN threshold.
A service is CRIT if any single metric exceeds the CRIT threshold.
Overall platform status = worst individual service status.

## Reporting Rule

OK status means all metrics are **within thresholds** — it does NOT mean absence of issues.
Always report actual numeric values. A service with `error_logs=2` is OK (below WARN>10)
but the finding MUST say "2 error logs (below WARN>10)" — never "No errors".
