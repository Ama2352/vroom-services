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
