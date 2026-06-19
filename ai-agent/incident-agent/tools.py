import os
import requests

EXECUTOR_URL = os.environ.get(
    "KUBECTL_EXECUTOR_URL",
    "http://kubectl-executor.monitoring.svc.cluster.local:5001"
)
EXECUTOR_TOKEN = os.environ.get("EXECUTOR_API_KEY", "change-me")

_READ_ENDPOINTS = {
    "get_pods":     "/tools/pods",
    "get_logs":     "/tools/logs",
    "get_events":   "/tools/events",
    "describe_pod": "/tools/describe",
    "get_metrics":  "/tools/metrics",
    "get_traces":   "/tools/traces",
}


def _headers() -> dict:
    return {"Authorization": f"Bearer {EXECUTOR_TOKEN}"}


def call_tool(tool_name: str, args: dict) -> str:
    if tool_name not in _READ_ENDPOINTS:
        return f"[unknown tool: {tool_name}]"

    endpoint = _READ_ENDPOINTS[tool_name]
    timeout = 5 if tool_name == "get_traces" else 35

    try:
        r = requests.get(f"{EXECUTOR_URL}{endpoint}", params=args, headers=_headers(), timeout=timeout)
    except Exception as e:
        return f"[tool error: {e}]"

    if r.status_code != 200:
        return f"[tool error: HTTP {r.status_code}]"

    data = r.json()
    return data.get("stdout") or data.get("error") or "[no output]"
