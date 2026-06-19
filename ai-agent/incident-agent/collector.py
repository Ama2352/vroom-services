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
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Ama2352/vroom-services")


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

    last_commit = "none"
    try:
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 6 * 3600))
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/commits",
            params={"since": since},
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=5,
        )
        commits = r.json() if r.ok and isinstance(r.json(), list) else []
        if commits:
            last_commit = commits[0]["commit"]["message"].split('\n')[0]
    except Exception:
        pass

    bundle = (f"service={service} namespace={namespace} "
              f"rps={rps} err={err}% p99={p99}s loki_errors={loki_errors}")
    if last_commit != "none":
        bundle += f' | last_commit: "{last_commit}"'
    return bundle
