"""Bounded runtime and workload-configuration diagnostics."""

import os
import re
import time

import requests as http_requests


PROMETHEUS_URL = os.environ.get(
    "PROMETHEUS_URL",
    "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090/prometheus/api/v1/query",
)
LOKI_URL = os.environ.get(
    "LOKI_URL",
    "http://loki-stack.monitoring.svc.cluster.local:3100/loki/api/v1/query_range",
)
EXECUTOR_URL = os.environ.get(
    "KUBECTL_EXECUTOR_URL",
    "http://kubectl-executor.monitoring.svc.cluster.local:5001",
)
EXECUTOR_TOKEN = os.environ.get("EXECUTOR_API_KEY", "change-me")
_IP_PORT_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3}):(\d+)\b")


def _prom_scalar(query: str) -> float:
    try:
        response = http_requests.get(PROMETHEUS_URL, params={"query": query}, timeout=5)
        results = response.json()["data"]["result"] if response.ok else []
        return float(results[0]["value"][1]) if results else 0.0
    except Exception:
        return 0.0


def _prom_active_label(query: str, label: str) -> str:
    try:
        response = http_requests.get(PROMETHEUS_URL, params={"query": query}, timeout=5)
        if not response.ok:
            return ""
        for item in response.json()["data"]["result"]:
            if float(item["value"][1]) == 1.0:
                return item["metric"].get(label, "")
    except Exception:
        pass
    return ""


def _loki_latest_error(service: str, namespace: str) -> str:
    try:
        now_ms = int(time.time() * 1000)
        response = http_requests.get(
            LOKI_URL,
            params={
                "query": f'{{app="{service}",namespace="{namespace}"}} |~ "(?i)(error|failed|panic|fatal|refused)"',
                "start": str((now_ms - 15 * 60 * 1000) * 1_000_000),
                "end": str(now_ms * 1_000_000),
                "limit": "1",
                "direction": "backward",
            },
            timeout=5,
        )
        values = response.json().get("data", {}).get("result", [])[0].get("values", []) if response.ok else []
        return values[0][1][:200] if values else ""
    except Exception:
        return ""


def _k8s_latest_warning(service: str, namespace: str) -> dict:
    try:
        response = http_requests.get(
            f"{EXECUTOR_URL}/tools/events-json",
            params={"namespace": namespace, "service": service},
            headers={"Authorization": f"Bearer {EXECUTOR_TOKEN}"},
            timeout=10,
        )
        events = response.json().get("events", []) if response.ok else []
        return events[-1] if events else {}
    except Exception:
        return {}


def collect_configuration_diff(service: str, namespace: str) -> dict:
    """Compare active pod revision with its verified predecessor, never image/commit history."""
    try:
        response = http_requests.get(
            f"{EXECUTOR_URL}/tools/workload-revisions",
            params={"service": service, "namespace": namespace},
            headers={"Authorization": f"Bearer {EXECUTOR_TOKEN}"},
            timeout=10,
        )
        payload = response.json() if response.ok else {}
    except Exception:
        return {"status": "unavailable", "changes": [], "reason": "executor_unavailable"}
    if payload.get("status") == "unavailable":
        return {"status": "unavailable", "changes": [], "reason": payload.get("reason", "unknown")}

    changes = []
    current, previous = payload.get("current") or {}, payload.get("previous") or {}
    for container in sorted(set(current) | set(previous)):
        for section in ("env", "resources"):
            before = ((previous.get(container) or {}).get(section) or {})
            after = ((current.get(container) or {}).get(section) or {})
            for key in sorted(set(before) | set(after)):
                if before.get(key) != after.get(key):
                    changes.append({
                        "path": f"containers.{container}.{section}.{key}",
                        "previous": before.get(key),
                        "current": after.get(key),
                    })
    return {
        "status": "changed" if changes else "unchanged",
        "changes": changes,
        "current_revision": payload.get("current_revision"),
    }


def resolve_dependency(log_error: str, event_message: str) -> dict | None:
    """Resolve a concrete service IP only; DNS resolver addresses are not dependencies."""
    match = _IP_PORT_RE.search(f"{log_error} {event_message}")
    if not match or match.group(2) == "53":
        return None
    try:
        response = http_requests.get(
            f"{EXECUTOR_URL}/tools/resolve-service",
            params={"ip": match.group(1)},
            headers={"Authorization": f"Bearer {EXECUTOR_TOKEN}"},
            timeout=10,
        )
        service = response.json() if response.ok else {}
    except Exception:
        return None
    if not service.get("name"):
        return None
    name, namespace = service["name"], service["namespace"]
    return {
        "name": name,
        "namespace": namespace,
        "pods_available": int(_prom_scalar(f'kube_deployment_status_replicas_available{{deployment="{name}",namespace="{namespace}"}}')),
        "pods_desired": int(_prom_scalar(f'kube_deployment_spec_replicas{{deployment="{name}",namespace="{namespace}"}}')),
        "waiting_reason": _prom_active_label(f'kube_pod_container_status_waiting_reason{{namespace="{namespace}",pod=~"{name}-.*"}}', "reason"),
    }


def collect_diagnostics(service: str, namespace: str) -> dict:
    """Collect service-level runtime facts with safe empty defaults."""
    event = _k8s_latest_warning(service, namespace)
    return {
        "pods_available": int(_prom_scalar(f'kube_deployment_status_replicas_available{{deployment="{service}",namespace="{namespace}"}}')),
        "pods_desired": int(_prom_scalar(f'kube_deployment_spec_replicas{{deployment="{service}",namespace="{namespace}"}}')),
        "pods_running": int(_prom_scalar(f'kube_deployment_status_replicas{{deployment="{service}",namespace="{namespace}"}}')),
        "pods_ready": int(_prom_scalar(f'kube_deployment_status_replicas_ready{{deployment="{service}",namespace="{namespace}"}}')),
        "waiting_reason": _prom_active_label(f'kube_pod_container_status_waiting_reason{{namespace="{namespace}",pod=~"{service}-.*"}}', "reason"),
        "last_terminated_reason": _prom_active_label(f'kube_pod_container_status_last_terminated_reason{{namespace="{namespace}",pod=~"{service}-.*"}}', "reason"),
        "restarts": int(_prom_scalar(f'sum(kube_pod_container_status_restarts_total{{namespace="{namespace}",pod=~"{service}-.*"}})')),
        "init_waiting_reason": _prom_active_label(f'kube_pod_init_container_status_waiting_reason{{namespace="{namespace}",pod=~"{service}-.*"}}', "reason"),
        "init_last_terminated_reason": _prom_active_label(f'kube_pod_init_container_status_last_terminated_reason{{namespace="{namespace}",pod=~"{service}-.*"}}', "reason"),
        "init_restarts": int(_prom_scalar(f'sum(kube_pod_init_container_status_restarts_total{{namespace="{namespace}",pod=~"{service}-.*"}})')),
        "log_error": _loki_latest_error(service, namespace),
        "event_reason": event.get("reason", ""),
        "event_message": event.get("message", ""),
        "event_object": event.get("object", ""),
    }
