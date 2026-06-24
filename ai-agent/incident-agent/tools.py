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
    stdout = data.get("stdout") or data.get("error") or "[no output]"

    if tool_name == "get_logs":
        HEALTH_PATTERNS = ("/healthz", "/readyz", "/metrics", "/health")
        ERROR_KEYWORDS  = ("error", "fail", "panic", "fatal", "exception", "crash", "exit code")
        filtered = [
            line for line in stdout.splitlines()
            if not any(p in line for p in HEALTH_PATTERNS)
        ]
        if not filtered:
            # Crash logs always contain an error keyword — fall back before giving up
            filtered = [
                line for line in stdout.splitlines()
                if any(k in line.lower() for k in ERROR_KEYWORDS)
            ]
        stdout = "\n".join(filtered) or "[no output after filtering health checks]"

    return stdout
