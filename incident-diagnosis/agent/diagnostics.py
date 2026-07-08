import os, re, time
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
GITHUB_TOKEN       = os.environ.get("GITHUB_TOKEN", "")
GITHUB_GITOPS_REPO = os.environ.get("GITHUB_GITOPS_REPO", "Ama2352/vroom-gitops")
GITHUB_API_URL     = "https://api.github.com"

_IP_PORT_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)\b")


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
            "query":     f'{{app="{service}",namespace="{namespace}"}} |~ "(?i)(error|failed|panic|fatal|refused)"',
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


def collect_change_evidence(service: str, namespace: str) -> dict | None:
    """Diff the 2 most recently created ReplicaSets for `service` — reveals a manual
    env-var hotfix (kubectl set env) or a new image tag shipped through the pipeline.
    Returns None if fewer than 2 ReplicaSets exist or neither image nor env differs."""
    try:
        r = http_requests.get(
            f"{EXECUTOR_URL}/tools/replicasets",
            params={"service": service, "namespace": namespace},
            headers={"Authorization": f"Bearer {EXECUTOR_TOKEN}"},
            timeout=10,
        )
        if not r.ok:
            return None
        items = r.json().get("items", [])
    except Exception:
        return None

    if len(items) < 2:
        return None

    items = sorted(items, key=lambda rs: rs.get("metadata", {}).get("creationTimestamp", ""))
    previous, newest = items[-2], items[-1]

    def _container(rs):
        containers = rs.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        return containers[0] if containers else {}

    def _env_map(container):
        return {e.get("name"): e.get("value", "") for e in container.get("env", [])}

    new_c, old_c = _container(newest), _container(previous)
    new_image, old_image = new_c.get("image", ""), old_c.get("image", "")
    new_env,   old_env   = _env_map(new_c),        _env_map(old_c)

    image_changed = bool(new_image) and bool(old_image) and new_image != old_image
    env_diff = [
        {"key": k, "old_value": old_env.get(k, ""), "new_value": v}
        for k, v in new_env.items()
        if old_env.get(k, "") != v
    ]
    env_changed = bool(env_diff)

    if not image_changed and not env_changed:
        return None

    return {
        "image_changed": image_changed, "old_image": old_image, "new_image": new_image,
        "env_changed": env_changed, "env_diff": env_diff,
        "changed_at": newest.get("metadata", {}).get("creationTimestamp", ""),
    }


def resolve_dependency(log_error: str, event_message: str) -> dict | None:
    """If log_error/event_message names an IP:port, resolve it to the K8s Service that
    owns that ClusterIP and report that Service's own pod health. Returns None if no
    IP is present or it doesn't resolve to a known Service (e.g. a DNS-name failure
    like 'bad-host' — that case is already covered by collect_change_evidence)."""
    m = _IP_PORT_RE.search(f"{log_error} {event_message}")
    if not m:
        return None
    ip, port = m.group(1), m.group(2)
    if port == "53":
        # Port 53 is always the cluster DNS resolver (CoreDNS/kube-dns), never an app
        # dependency in this project — Go's net package prints this address on every
        # "no such host" lookup failure regardless of cause, so it's not causal signal.
        return None

    try:
        r = http_requests.get(
            f"{EXECUTOR_URL}/tools/resolve-service",
            params={"ip": ip},
            headers={"Authorization": f"Bearer {EXECUTOR_TOKEN}"},
            timeout=10,
        )
        if not r.ok:
            return None
        svc = r.json()
    except Exception:
        return None

    if not svc.get("name"):
        return None

    dep_available = int(_prom_scalar(
        f'kube_deployment_status_replicas_available{{deployment="{svc["name"]}",namespace="{svc["namespace"]}"}}'
    ))
    dep_desired = int(_prom_scalar(
        f'kube_deployment_spec_replicas{{deployment="{svc["name"]}",namespace="{svc["namespace"]}"}}'
    ))
    dep_waiting = _prom_active_label(
        f'kube_pod_container_status_waiting_reason{{namespace="{svc["namespace"]}",pod=~"{svc["name"]}-.*"}}',
        label="reason",
    )
    return {
        "name": svc["name"], "namespace": svc["namespace"],
        "pods_available": dep_available, "pods_desired": dep_desired,
        "waiting_reason": dep_waiting,
    }


def _short_name(service: str) -> str:
    return service[:-len("-service")] if service.endswith("-service") else service


def _env_name(namespace: str) -> str:
    return namespace[len("vroom-"):] if namespace.startswith("vroom-") else namespace


def _argocd_app_name(service: str, namespace: str) -> str:
    return f"vroom-{_env_name(namespace)}-{_short_name(service)}"


def _gitops_file_path(service: str, namespace: str, template_diff: dict) -> str:
    short = _short_name(service)
    if template_diff.get("image_changed"):
        return f"apps/{short}/overlays/{_env_name(namespace)}/kustomization.yaml"
    return f"apps/{short}/base/deployment.yaml"


def _github_headers() -> dict:
    return {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}


def collect_provenance(service: str, namespace: str, template_diff: dict | None) -> dict | None:
    """Attribute a template_diff to either a manual hotfix (ArgoCD OutOfSync — the live
    change bypassed GitOps) or the git commit that introduced it (ArgoCD Synced). Returns
    None when there's no template_diff to attribute in the first place."""
    if template_diff is None:
        return None

    app_name = _argocd_app_name(service, namespace)
    try:
        r = http_requests.get(
            f"{EXECUTOR_URL}/tools/argocd-sync",
            params={"app": app_name},
            headers={"Authorization": f"Bearer {EXECUTOR_TOKEN}"},
            timeout=10,
        )
        data = r.json() if r.ok else {}
        sync_status = data.get("sync_status", "Unknown")
        raw_app = data.get("raw", {})
        synced_sha = raw_app.get("status", {}).get("sync", {}).get("revision", "")
    except Exception:
        sync_status = "Unknown"
        synced_sha = ""

    if sync_status != "Synced":
        return {"classification": "hotfix", "changed_at": template_diff.get("changed_at", "")}

    if not synced_sha:
        return {"classification": "gitops-commit", "commit": None, "pr": None}

    file_path = _gitops_file_path(service, namespace, template_diff)
    try:
        r = http_requests.get(
            f"{GITHUB_API_URL}/repos/{GITHUB_GITOPS_REPO}/commits",
            params={"path": file_path, "sha": synced_sha, "per_page": 1},
            headers=_github_headers(),
            timeout=10,
        )
        commits = r.json() if r.ok else []
    except Exception:
        commits = []

    if not commits:
        return {"classification": "gitops-commit", "commit": None, "pr": None}

    sha = commits[0]["sha"]
    try:
        r = http_requests.get(
            f"{GITHUB_API_URL}/repos/{GITHUB_GITOPS_REPO}/commits/{sha}",
            headers=_github_headers(), timeout=10,
        )
        detail = r.json() if r.ok else {}
    except Exception:
        detail = {}

    diff_snippet = ""
    for f in detail.get("files", []):
        if f.get("filename") == file_path:
            diff_snippet = f.get("patch", "")
            break

    commit_info = {
        "sha":          sha[:7],
        "author":       (detail.get("commit") or {}).get("author", {}).get("name", ""),
        "message":      (detail.get("commit") or {}).get("message", ""),
        "date":         (detail.get("commit") or {}).get("author", {}).get("date", ""),
        "url":          detail.get("html_url", ""),
        "diff_snippet": diff_snippet,
    }

    pr_info = None
    try:
        r = http_requests.get(
            f"{GITHUB_API_URL}/repos/{GITHUB_GITOPS_REPO}/commits/{sha}/pulls",
            headers={**_github_headers(), "Accept": "application/vnd.github.groot-preview+json"},
            timeout=10,
        )
        prs = r.json() if r.ok else []
        if prs:
            pr_info = {"number": prs[0]["number"], "title": prs[0]["title"], "url": prs[0]["html_url"]}
    except Exception:
        pass

    return {"classification": "gitops-commit", "commit": commit_info, "pr": pr_info}


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
    """Build a human-readable evidence snippet from structured facts — up to 6 lines
    when pod, init-container, log, event, template-diff, and dependency facts are all
    present at once (typically far fewer).

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

    if facts.get("template_diff"):
        td = facts["template_diff"]
        if td.get("env_changed"):
            first = td["env_diff"][0]
            lines.append(
                f"Recent change: env {first['key']} changed from "
                f"{first['old_value']} to {first['new_value']}"
            )
        elif td.get("image_changed"):
            lines.append(f"Recent change: image changed from {td['old_image']} to {td['new_image']}")

    if facts.get("dependency"):
        dep = facts["dependency"]
        dep_line = f"Dependency {dep['name']}: {dep['pods_available']}/{dep['pods_desired']} pods running"
        if dep.get("waiting_reason"):
            dep_line += f" ({dep['waiting_reason']})"
        lines.append(dep_line)

    return "\n".join(lines) if lines else "No diagnostic data available"
