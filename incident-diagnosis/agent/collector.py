import os, time
import requests

PROMETHEUS_URL = os.environ.get(
    "PROMETHEUS_URL",
    "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090/prometheus/api/v1/query"
)
LOKI_URL = os.environ.get(
    "LOKI_URL",
    "http://loki-stack.monitoring.svc.cluster.local:3100/loki/api/v1/query_range"
)
TEMPO_URL = os.environ.get(
    "TEMPO_URL",
    "http://tempo.monitoring.svc.cluster.local:3100"
)


def _prom_value(query: str):
    """Return (value, status, error) without converting missing data to zero."""
    try:
        response = requests.get(PROMETHEUS_URL, params={"query": query}, timeout=5)
        if not response.ok:
            return None, "unavailable", f"Prometheus returned HTTP {response.status_code}"
        results = response.json()["data"]["result"]
        if not results:
            return None, "no_data", None
        return float(results[0]["value"][1]), "available", None
    except Exception as exc:
        return None, "unavailable", str(exc)


def collect_impact(service: str, namespace: str, window: str = "5m", alert: dict | None = None) -> dict:
    """Collect service-scoped HTTP impact, retaining the alert's own metric when applicable."""
    total_query = (
        f'sum(rate(gin_requests_total{{service="{service}",namespace="{namespace}"}}[{window}]))'
    )
    error_query = (
        f'sum(rate(gin_requests_total{{service="{service}",namespace="{namespace}",code=~"5.."}}[{window}]))'
    )
    p99_query = (
        "histogram_quantile(0.99, sum by (le) "
        f'(rate(gin_request_duration_seconds_bucket{{service="{service}",namespace="{namespace}"}}[{window}])))'
    )

    values = []
    errors = []
    for name, query in (("request_rate", total_query), ("error_rate", error_query), ("p99_seconds", p99_query)):
        value, status, error = _prom_value(query)
        values.append((name, value, status))
        if error:
            errors.append(f"{name}: {error}")

    statuses = {status for _, _, status in values}
    if "unavailable" in statuses:
        status = "unavailable"
    elif "no_data" in statuses:
        status = "no_data"
    else:
        status = "available"

    parsed = {name: value for name, value, _ in values}
    total = parsed["request_rate"]
    error_rate = parsed["error_rate"]
    error_percent = None if total is None or error_rate is None else (
        0.0 if total == 0.0 else error_rate / total * 100
    )
    impact = {
        "status": status,
        "window": window,
        "request_rate": total,
        "error_rate_percent": error_percent,
        "p99_seconds": parsed["p99_seconds"],
        "errors": errors,
    }
    if (alert or {}).get("alert_name") == "DLQEventsDetected":
        metric_value = (alert or {}).get("metric_value")
        threshold = (alert or {}).get("threshold")
        if metric_value is None:
            metric_value, metric_status, metric_error = _prom_value(
                f'increase(vroom_dlq_events_total{{namespace="{namespace}"}}[5m])'
            )
            if metric_error:
                errors.append(f"dlq_events: {metric_error}")
        if metric_value is not None:
            impact["status"] = "available"
            impact["triggering_metric"] = {
                "name": "DLQ events", "value": metric_value, "threshold": 0.0 if threshold is None else threshold,
            }
    return impact

def _prom(query: str) -> float:
    try:
        r = requests.get(PROMETHEUS_URL, params={"query": query}, timeout=5)
        results = r.json()["data"]["result"] if r.ok else []
        return float(results[0]["value"][1]) if results else 0.0
    except Exception:
        return 0.0


def collect_bundle(service: str, namespace: str) -> str:
    now_ms = int(time.time() * 1000)

    rps = round(_prom(f'rate(http_requests_total{{app="{service}"}}[5m])'), 1)
    err = round(_prom(
        f'rate(http_requests_total{{app="{service}",status=~"5.."}}[5m])'
        f' / rate(http_requests_total{{app="{service}"}}[5m]) * 100'
    ), 2)
    p99 = round(_prom(
        f'histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{{app="{service}"}}[5m]))'
    ), 3)

    loki_errors = 0
    try:
        r = requests.get(LOKI_URL, params={
            "query": f'{{app="{service}"}} |= "error"',
            "start": str((now_ms - 15 * 60 * 1000) * 1_000_000),
            "end":   str(now_ms * 1_000_000),
            "limit": "50",
        }, timeout=5)
        loki_errors = len(r.json().get("data", {}).get("result", [])) if r.ok else 0
    except Exception:
        pass

    traces_errored, trace_sample = 0, ""
    try:
        r = requests.get(f"{TEMPO_URL}/api/search", params={
            "tags":  f"service.name={service}&error=true",
            "start": f"{(now_ms - 900000) * 1_000_000}",
            "end":   f"{now_ms * 1_000_000}",
            "limit": "3",
        }, timeout=2)
        if r.ok:
            traces         = r.json().get("traces", [])
            traces_errored = len(traces)
            trace_sample   = traces[0].get("rootTraceName", "") if traces else ""
    except Exception:
        pass

    bundle = (f"service={service} namespace={namespace} "
              f"rps={rps} err={err}% p99={p99}s loki_errors={loki_errors} "
              f"traces_errored={traces_errored}")
    if trace_sample:
        bundle += f' (sample: "{trace_sample}")'
    return bundle
