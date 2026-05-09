# Anomaly Thresholds

| Metric | WARN | CRIT |
|--------|------|------|
| error_rate_pct | > 1.0% | > 5.0% |
| p99_latency_s  | > 2.0s | > 5.0s |
| error_log_count (30 min window) | > 10 | > 50 |

A service is WARN if any single metric exceeds the WARN threshold.
A service is CRIT if any single metric exceeds the CRIT threshold.
Overall platform status = worst individual service status.
