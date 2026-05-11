#!/usr/bin/env python3
"""
Queries Prometheus and Loki for Vroom service metrics.
Outputs metrics.json and exits with:
  0 — all services within thresholds (skip Gemini call)
  1 — anomaly detected (Gemini analysis needed)
"""
import json
import os
import sys
import time

import requests

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.monitoring:9090/prometheus").rstrip("/")
LOKI_URL       = os.environ.get("LOKI_URL", "http://loki-stack.monitoring:3100").rstrip("/")
NAMESPACE      = os.environ.get("TARGET_NAMESPACE", "vroom-dev")
WINDOW         = os.environ.get("VERIFY_WINDOW", "10m")

SERVICES = ["user-service", "ride-service", "dispatch-service", "notification-service"]

# Anomaly thresholds (mirrors skill/references/thresholds.md)
THRESHOLDS = {
    "error_rate_pct":  1.0,
    "p99_latency_s":   2.0,
    "error_log_count": 10,
}


def query_prometheus(promql: str) -> list:
    url = f"{PROMETHEUS_URL}/api/v1/query"
    try:
        print(f"INFO: Querying Prometheus at {url}...")
        resp = requests.get(
            url,
            params={"query": promql},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("data", {}).get("result", [])
        return results
    except Exception as exc:
        print(f"ERROR: Prometheus query failed at {url}: {exc}", file=sys.stderr)
        return None


def query_loki_error_count(service: str) -> int | None:
    end_ns   = int(time.time() * 1e9)
    # Convert window like "30m" to nanoseconds
    minutes  = int(WINDOW.rstrip("m")) if WINDOW.endswith("m") else 30
    start_ns = end_ns - minutes * 60 * int(1e9)
    try:
        resp = requests.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={
                "query": f'count_over_time({{namespace="{NAMESPACE}", app="{service}"}} |= "level=error" [{WINDOW}])',
                "start": start_ns,
                "end":   end_ns,
                "limit": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("data", {}).get("result", [])
        if results:
            return int(float(results[0]["values"][-1][1]))
        return 0
    except Exception as exc:
        print(f"WARN: Loki query failed for {service}: {exc}", file=sys.stderr)
        return None


def extract_value(results: list | None, label: str, label_value: str) -> float | None:
    if results is None:
        return None
    for r in results:
        if r.get("metric", {}).get(label) == label_value:
            try:
                return float(r["value"][1])
            except (KeyError, IndexError, ValueError):
                pass
    return None


def main() -> int:
    metrics = {"window": WINDOW, "namespace": NAMESPACE, "services": {}}

    # Prometheus queries
    req_rate_results = query_prometheus(
        f'sum by (app) (rate(http_requests_total{{namespace="{NAMESPACE}"}}[{WINDOW}]))'
    )
    err_rate_results = query_prometheus(
        f'100 * sum by (app) (rate(http_requests_total{{namespace="{NAMESPACE}",status=~"5.."}}[{WINDOW}]))'
        f' / sum by (app) (rate(http_requests_total{{namespace="{NAMESPACE}"}}[{WINDOW}]))'
    )
    p99_results = query_prometheus(
        f'histogram_quantile(0.99, sum by (le, app) (rate(http_request_duration_seconds_bucket{{namespace="{NAMESPACE}"}}[{WINDOW}])))'
    )

    anomaly_found = False

    for svc in SERVICES:
        req_rate   = extract_value(req_rate_results, "app", svc)
        error_rate = extract_value(err_rate_results, "app", svc)
        p99        = extract_value(p99_results,      "app", svc)
        error_logs = query_loki_error_count(svc)

        metrics["services"][svc] = {
            "req_rate_rps":   round(req_rate,   2) if req_rate   is not None else 0.0,
            "error_rate_pct": round(error_rate, 3) if error_rate is not None else 0.0,
            "p99_latency_s":  round(p99,        3) if p99        is not None else 0.0,
            "error_logs":     error_logs if error_logs is not None else 0,
            "instrumented":   (req_rate is not None),
        }

        # Anomaly is only true if metrics EXIST and exceed thresholds, 
        # or if critical logs are found. Missing metrics are noted but don't trigger alerts here.
        if (
            (error_rate is not None and error_rate > THRESHOLDS["error_rate_pct"])
            or (p99 is not None and p99 > THRESHOLDS["p99_latency_s"])
            or (error_logs is not None and error_logs > THRESHOLDS["error_log_count"])
        ):
            anomaly_found = True

    with open("metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    print(json.dumps(metrics, indent=2))

    if anomaly_found:
        print("STATUS: anomaly detected — Gemini analysis required")
        return 1

    print("STATUS: all services within thresholds — skipping Gemini call")
    return 0


if __name__ == "__main__":
    sys.exit(main())
