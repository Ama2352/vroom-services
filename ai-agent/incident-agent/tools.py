import os, re
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

# Timestamp prefixes to strip before dedup comparison
_TS_RE = re.compile(
    r'^(?:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\s*'
    r'|\[GIN\]\s+\d{4}/\d{2}/\d{2}\s+-\s+\d{2}:\d{2}:\d{2}\s*'
    r'|\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s*)'
)
_HTTP_5XX_RE  = re.compile(r'\|\s*5\d\d\s*\|')
_HEALTH_PATHS = ("/healthz", "/readyz", "/metrics", "/health")
_HEALTH_2XX   = ("| 200 |", "| 204 |", '" 200"', '" 204"', ' 200 ')


def _is_health_success(line: str) -> bool:
    """True if line is a routine health-endpoint 200/204 response — pure noise."""
    return any(p in line for p in _HEALTH_PATHS) and any(s in line for s in _HEALTH_2XX)


def _is_error_signal(line: str) -> bool:
    """True if line has a structured error level or HTTP 5xx status."""
    l = line.lower()
    return (
        'level=error' in l or 'level=warn' in l or
        '"level":"error"' in l or '"level":"warn"' in l or
        bool(_HTTP_5XX_RE.search(line))
    )


def _dedup_key(line: str) -> str:
    """Strip leading timestamp so identical messages with different timestamps match."""
    return _TS_RE.sub('', line).strip()


def _process_logs(stdout: str) -> str:
    """Filter and deduplicate log output.

    Layer 2 — exclude health-endpoint 200/204 responses; prefer error-signal lines.
    Layer 3 — collapse repeated messages to '[×N] message'.
    """
    lines = stdout.splitlines()
    non_noise   = [l for l in lines if not _is_health_success(l)]
    error_lines = [l for l in non_noise if _is_error_signal(l)]
    candidates  = error_lines if error_lines else non_noise

    seen: dict[str, int] = {}
    ordered: list[str]   = []
    for line in candidates:
        key = _dedup_key(line)
        if key in seen:
            seen[key] += 1
        else:
            seen[key] = 1
            ordered.append(line)

    result = []
    for line in ordered:
        count = seen[_dedup_key(line)]
        result.append(f"[×{count}] {line}" if count > 1 else line)
    return "\n".join(result) or "[no output after filtering health checks]"


def _headers() -> dict:
    return {"Authorization": f"Bearer {EXECUTOR_TOKEN}"}


def call_tool(tool_name: str, args: dict) -> str:
    if tool_name not in _READ_ENDPOINTS:
        return f"[unknown tool: {tool_name}]"

    endpoint = _READ_ENDPOINTS[tool_name]
    timeout  = 15 if tool_name == "get_traces" else 35

    try:
        r = requests.get(f"{EXECUTOR_URL}{endpoint}", params=args, headers=_headers(), timeout=timeout)
    except Exception as e:
        return f"[tool error: {e}]"

    if r.status_code != 200:
        return f"[tool error: HTTP {r.status_code}]"

    data   = r.json()
    stdout = data.get("stdout") or data.get("error") or "[no output]"

    if tool_name == "get_logs":
        stdout = _process_logs(stdout)

    return stdout
