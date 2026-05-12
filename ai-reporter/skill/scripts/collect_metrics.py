#!/usr/bin/env python3
"""
Queries Prometheus and Loki for Vroom service metrics.

Flags:
  --baseline   Collect pre-deploy baseline metrics → baseline_metrics.json
               (uses BASELINE_WINDOW env var, exits 0 always — non-fatal)

Env vars:
  MOCK_METRICS_FILE  If set, skip all queries and copy the file to metrics.json (for testing)
  PROMETHEUS_URL     Default: http://kube-prometheus-stack-prometheus.monitoring:9090/prometheus
  LOKI_URL           Default: http://loki-stack.monitoring:3100
  TARGET_NAMESPACE   Default: vroom-dev
  VERIFY_WINDOW      Query window for current metrics (default: 10m)
  BASELINE_WINDOW    Query window for baseline metrics (default: 30m)

Exit codes (current metrics mode):
  0 — all services within thresholds
  1 — anomaly detected (threshold exceeded, error_logs > 0, or pod health issue)

Exit code (baseline mode): always 0
"""
import json
import os
import shutil
import sys
import time

import requests

PROMETHEUS_URL  = os.environ.get("PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.monitoring:9090/prometheus").rstrip("/")
LOKI_URL        = os.environ.get("LOKI_URL", "http://loki-stack.monitoring:3100").rstrip("/")
NAMESPACE       = os.environ.get("TARGET_NAMESPACE", "vroom-dev")
WINDOW          = os.environ.get("VERIFY_WINDOW", "10m")
BASELINE_WINDOW = os.environ.get("BASELINE_WINDOW", "30m")

SERVICES = ["user-service", "ride-service", "dispatch-service", "notification-service"]

THRESHOLDS = {
    "error_rate_pct":  1.0,
    "p99_latency_s":   2.0,
    "error_log_count": 10,
}


def _window_to_minutes(w: str) -> int:
    try:
        if w.endswith("m"):
            return int(w[:-1])
        if w.endswith("h"):
            return int(w[:-1]) * 60
    except ValueError:
        pass
    return 30


def query_prometheus(promql: str) -> list | None:
    url = f"{PROMETHEUS_URL}/api/v1/query"
    try:
        resp = requests.get(url, params={"query": promql}, timeout=10)
        resp.raise_for_status()
        return resp.json().get("data", {}).get("result", [])
    except Exception as exc:
        print(f"WARN: Prometheus query failed: {exc}", file=sys.stderr)
        return None


def extract_value(results: list | None, label: str, label_value: str) -> float | None:
    if not results:
        return None
    for r in results:
        if r.get("metric", {}).get(label) == label_value:
            try:
                return float(r["value"][1])
            except (KeyError, IndexError, ValueError):
                pass
    return None


def query_loki_error_count(service: str, window: str) -> int | None:
    end_ns   = int(time.time() * 1e9)
    minutes  = _window_to_minutes(window)
    start_ns = end_ns - minutes * 60 * int(1e9)
    try:
        resp = requests.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={
                "query": f'count_over_time({{namespace="{NAMESPACE}", app="{service}"}} |~ "(?i)error" [{window}])',
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
        print(f"WARN: Loki error count query failed for {service}: {exc}", file=sys.stderr)
        return None


def fetch_error_log_samples(service: str, window: str, limit: int = 5) -> list[str]:
    """Fetch actual log lines from Loki when error_logs > 0. Non-fatal."""
    end_ns   = int(time.time() * 1e9)
    minutes  = _window_to_minutes(window)
    start_ns = end_ns - minutes * 60 * int(1e9)
    try:
        resp = requests.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={
                "query":     f'{{namespace="{NAMESPACE}", app="{service}"}} |~ "(?i)error"',
                "start":     str(start_ns),
                "end":       str(end_ns),
                "limit":     str(limit),
                "direction": "backward",
            },
            timeout=10,
        )
        resp.raise_for_status()
        samples = []
        for stream in resp.json().get("data", {}).get("result", []):
            for _ts, line in stream.get("values", []):
                samples.append(line[:150])
        return samples[:limit]
    except Exception as exc:
        print(f"WARN: Loki log sample fetch failed for {service}: {exc}", file=sys.stderr)
        return []


def collect_pod_health(namespace: str) -> dict:
    """Query Prometheus for pod restart counts and OOMKills per service."""
    restart_results = query_prometheus(
        f'sum by (pod) (kube_pod_container_status_restarts_total{{namespace="{namespace}"}})'
    )
    oomkill_results = query_prometheus(
        f'kube_pod_container_status_last_terminated_reason{{namespace="{namespace}",reason="OOMKilled"}}'
    )
    pod_health = {}
    for svc in SERVICES:
        restarts = 0
        oomkills = 0
        if restart_results:
            for r in restart_results:
                pod_name = r.get("metric", {}).get("pod", "")
                if svc in pod_name:
                    try:
                        restarts += int(float(r["value"][1]))
                    except (KeyError, IndexError, ValueError):
                        pass
        if oomkill_results:
            for r in oomkill_results:
                pod_name = r.get("metric", {}).get("pod", "")
                if svc in pod_name:
                    try:
                        oomkills += int(float(r["value"][1]))
                    except (KeyError, IndexError, ValueError):
                        pass
        pod_health[svc] = {"restart_count": restarts, "oomkill_count": oomkills}
    return pod_health


def collect_for_window(window: str) -> tuple[dict, bool]:
    """Collect metrics for a given window. Returns (metrics_dict, anomaly_found)."""
    req_rate_results = query_prometheus(
        f'sum by (service) (rate(gin_requests_total{{namespace="{NAMESPACE}"}}[{window}]))'
    )
    err_rate_results = query_prometheus(
        f'100 * sum by (service) (rate(gin_requests_total{{namespace="{NAMESPACE}",code=~"5.."}}[{window}]))'
        f' / sum by (service) (rate(gin_requests_total{{namespace="{NAMESPACE}"}}[{window}]))'
    )
    p99_results = query_prometheus(
        f'histogram_quantile(0.99, sum by (le, service) (rate(gin_request_duration_seconds_bucket{{namespace="{NAMESPACE}"}}[{window}])))'
    )
    pod_health = collect_pod_health(NAMESPACE)

    anomaly_found = False
    services = {}

    for svc in SERVICES:
        req_rate   = extract_value(req_rate_results, "service", svc)
        error_rate = extract_value(err_rate_results, "service", svc)
        p99        = extract_value(p99_results,      "service", svc)
        error_logs = query_loki_error_count(svc, window)

        # Fetch actual log lines if errors present
        error_log_samples = []
        if error_logs and error_logs > 0:
            error_log_samples = fetch_error_log_samples(svc, window)

        svc_pod_health = pod_health.get(svc, {"restart_count": 0, "oomkill_count": 0})

        services[svc] = {
            "req_rate_rps":      round(req_rate,   2) if req_rate   is not None else 0.0,
            "error_rate_pct":    round(error_rate, 3) if error_rate is not None else 0.0,
            "p99_latency_s":     round(p99,        3) if p99        is not None else 0.0,
            "error_logs":        error_logs if error_logs is not None else 0,
            "error_log_samples": error_log_samples,
            "pod_health":        svc_pod_health,
            "instrumented":      (req_rate is not None),
        }

        if (
            (error_rate is not None and error_rate > THRESHOLDS["error_rate_pct"])
            or (p99 is not None and p99 > THRESHOLDS["p99_latency_s"])
            or (error_logs is not None and error_logs > THRESHOLDS["error_log_count"])
            or svc_pod_health.get("oomkill_count", 0) > 0
            or svc_pod_health.get("restart_count", 0) > 0
        ):
            anomaly_found = True

    return {"window": window, "namespace": NAMESPACE, "services": services}, anomaly_found


def main() -> int:
    baseline_mode = "--baseline" in sys.argv

    # Mock mode: skip all queries (for offline testing)
    mock_file = os.environ.get("MOCK_METRICS_FILE", "")
    if mock_file:
        out_path = "baseline_metrics.json" if baseline_mode else "metrics.json"
        shutil.copy(mock_file, out_path)
        print(f"STATUS: using mock metrics from {mock_file} → {out_path}")
        return 0

    if baseline_mode:
        # Collect pre-deploy baseline — always non-fatal
        metrics, _ = collect_for_window(BASELINE_WINDOW)
        with open("baseline_metrics.json", "w") as f:
            json.dump(metrics, f, indent=2)
        print(f"STATUS: baseline metrics collected (window={BASELINE_WINDOW})")
        return 0

    # Normal current-metrics collection
    metrics, anomaly_found = collect_for_window(WINDOW)

    with open("metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    print(json.dumps(metrics, indent=2))

    if anomaly_found:
        print("STATUS: anomaly detected — Gemini analysis required")
        return 1

    print("STATUS: all services within thresholds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
