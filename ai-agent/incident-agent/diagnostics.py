import os, time
import requests as http_requests

PROMETHEUS_URL = os.environ.get(
    "PROMETHEUS_URL",
    "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090/prometheus/api/v1/query"
)
LOKI_URL = os.environ.get(
    "LOKI_URL",
    "http://loki-stack.monitoring.svc.cluster.local:3100/loki/api/v1/query_range"
)
EXECUTOR_URL   = os.environ.get("KUBECTL_EXECUTOR_URL",
                                "http://kubectl-executor.monitoring.svc.cluster.local:5001")
EXECUTOR_TOKEN = os.environ.get("EXECUTOR_API_KEY", "change-me")


def _prom_scalar(query: str) -> float:
    try:
        r = http_requests.get(PROMETHEUS_URL, params={"query": query}, timeout=5)
        results = r.json()["data"]["result"] if r.ok else []
        return float(results[0]["value"][1]) if results else 0.0
    except Exception:
        return 0.0


def _prom_active_label(query: str, label: str) -> str:
    """Return the label value of the first Prometheus series whose metric value equals 1."""
    try:
        r = http_requests.get(PROMETHEUS_URL, params={"query": query}, timeout=5)
        if not r.ok:
            return ""
        for item in r.json()["data"]["result"]:
            if float(item["value"][1]) == 1.0:
                return item["metric"].get(label, "")
        return ""
    except Exception:
        return ""


def _loki_latest_error(service: str, namespace: str) -> str:
    try:
        now_ms = int(time.time() * 1000)
        r = http_requests.get(LOKI_URL, params={
            "query":     f'{{app="{service}",namespace="{namespace}"}} |= "error"',
            "start":     str((now_ms - 15 * 60 * 1000) * 1_000_000),
            "end":       str(now_ms * 1_000_000),
            "limit":     "1",
            "direction": "backward",
        }, timeout=5)
        if not r.ok:
            return ""
        results = r.json().get("data", {}).get("result", [])
        if results:
            values = results[0].get("values", [])
            if values:
                return values[0][1][:200]
    except Exception:
        pass
    return ""


def _k8s_latest_warning(service: str, namespace: str) -> dict:
    try:
        r = http_requests.get(
            f"{EXECUTOR_URL}/tools/events-json",
            params={"namespace": namespace, "service": service},
            headers={"Authorization": f"Bearer {EXECUTOR_TOKEN}"},
            timeout=10,
        )
        if not r.ok:
            return {}
        events = r.json().get("events", [])
        return events[-1] if events else {}
    except Exception:
        return {}


def collect_diagnostics(service: str, namespace: str) -> dict:
    """Fetch structured pod diagnostics from Prometheus, Loki, and K8s Events API.

    All fields have safe empty/zero defaults — source unavailability is not an error.
    Queries at deployment/service level; never by pod name.
    """
    pods_available = int(_prom_scalar(
        f'kube_deployment_status_replicas_available{{deployment="{service}",namespace="{namespace}"}}'
    ))
    pods_desired = int(_prom_scalar(
        f'kube_deployment_spec_replicas{{deployment="{service}",namespace="{namespace}"}}'
    ))
    waiting_reason = _prom_active_label(
        f'kube_pod_container_status_waiting_reason{{namespace="{namespace}",pod=~"{service}-.*"}}',
        label="reason",
    )
    last_terminated_reason = _prom_active_label(
        f'kube_pod_container_status_last_terminated_reason{{namespace="{namespace}",pod=~"{service}-.*"}}',
        label="reason",
    )
    restarts = int(_prom_scalar(
        f'sum(kube_pod_container_status_restarts_total{{namespace="{namespace}",pod=~"{service}-.*"}})'
    ))
    # Init containers are tracked separately — PodInitializing on the main container
    # means an init container is still running or crashing.
    init_waiting_reason = _prom_active_label(
        f'kube_pod_init_container_status_waiting_reason{{namespace="{namespace}",pod=~"{service}-.*"}}',
        label="reason",
    )
    init_last_terminated_reason = _prom_active_label(
        f'kube_pod_init_container_status_last_terminated_reason{{namespace="{namespace}",pod=~"{service}-.*"}}',
        label="reason",
    )
    init_restarts = int(_prom_scalar(
        f'sum(kube_pod_init_container_status_restarts_total{{namespace="{namespace}",pod=~"{service}-.*"}})'
    ))
    log_error = _loki_latest_error(service, namespace)
    event     = _k8s_latest_warning(service, namespace)

    return {
        "pods_available":             pods_available,
        "pods_desired":               pods_desired,
        "waiting_reason":             waiting_reason,
        "last_terminated_reason":     last_terminated_reason,
        "restarts":                   restarts,
        "init_waiting_reason":        init_waiting_reason,
        "init_last_terminated_reason": init_last_terminated_reason,
        "init_restarts":              init_restarts,
        "log_error":                  log_error,
        "event_reason":               event.get("reason",   ""),
        "event_message":              event.get("message",  ""),
        "event_object":               event.get("object",   ""),
    }


def format_evidence(facts: dict) -> str:
    """Build a max-3-line human-readable evidence snippet from structured facts.

    Pure dict access — no regex, no text parsing.
    """
    lines = []

    pod_line = f"Pods: {facts['pods_available']}/{facts['pods_desired']} running"
    if facts.get("waiting_reason"):
        pod_line += f" ({facts['waiting_reason']}, {facts['restarts']} restarts)"
        if facts.get("last_terminated_reason"):
            pod_line += f" [last exit: {facts['last_terminated_reason']}]"
    elif facts.get("restarts", 0) > 0:
        pod_line += f" ({facts['restarts']} restarts)"
        if facts.get("last_terminated_reason"):
            pod_line += f" [last exit: {facts['last_terminated_reason']}]"
    lines.append(pod_line)

    if facts.get("init_waiting_reason") or facts.get("init_last_terminated_reason"):
        init_line = f"Init container: {facts.get('init_waiting_reason') or 'waiting'}"
        init_line += f" ({facts.get('init_restarts', 0)} restarts)"
        if facts.get("init_last_terminated_reason"):
            init_line += f" — last exit: {facts['init_last_terminated_reason']}"
        lines.append(init_line)

    if facts.get("log_error"):
        lines.append(f"Error: {facts['log_error'][:120]}")

    if facts.get("event_reason"):
        parts = [f"Event: {facts['event_reason']}"]
        if facts.get("event_object"):
            parts.append(f"on {facts['event_object']}")
        if facts.get("event_message"):
            parts.append(f"— {facts['event_message'][:80]}")
        lines.append(" ".join(parts))

    return "\n".join(lines) if lines else "No diagnostic data available"
