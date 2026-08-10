import os, re, time
from datetime import datetime, timezone
import requests as http_requests

from provenance import select_gitops_change

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
_VERSIONED_MESSAGE_FIELD_MARKERS = ("EVENT", "MESSAGE", "PAYLOAD")


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

    changed_at = newest.get("metadata", {}).get("creationTimestamp", "")
    try:
        r_dep = http_requests.get(
            f"{EXECUTOR_URL}/tools/deployment",
            params={"service": service, "namespace": namespace},
            headers={"Authorization": f"Bearer {EXECUTOR_TOKEN}"},
            timeout=10,
        )
        if r_dep.ok:
            conditions = r_dep.json().get("deployment", {}).get("status", {}).get("conditions", [])
            for cond in conditions:
                if cond.get("type") == "Progressing" and cond.get("lastUpdateTime"):
                    changed_at = cond["lastUpdateTime"]
                    break
    except Exception:
        pass

    return {
        "image_changed": image_changed, "old_image": old_image, "new_image": new_image,
        "env_changed": env_changed, "env_diff": env_diff,
        "changed_at": changed_at,
    }


def collect_configuration_diff(service: str, namespace: str) -> dict:
    """Compare the active workload revision with its verified predecessor."""
    try:
        response = http_requests.get(
            f"{EXECUTOR_URL}/tools/workload-revisions",
            params={"service": service, "namespace": namespace},
            headers={"Authorization": f"Bearer {EXECUTOR_TOKEN}"},
            timeout=10,
        )
        if not response.ok:
            return {"status": "unavailable", "changes": [], "reason": "executor_unavailable"}
        payload = response.json()
    except Exception:
        return {"status": "unavailable", "changes": [], "reason": "executor_unavailable"}
    if payload.get("status") == "unavailable":
        return {"status": "unavailable", "changes": [], "reason": payload.get("reason", "unknown")}
    current = payload.get("current") or {}
    previous = payload.get("previous") or {}
    changes = []
    for container in sorted(set(current) | set(previous)):
        cur = current.get(container) or {}
        old = previous.get(container) or {}
        for key in ("env", "resources"):
            cur_values = cur.get(key) or {}
            old_values = old.get(key) or {}
            if key == "resources":
                cur_values = {str(k): str(v) for k, v in cur_values.items()}
                old_values = {str(k): str(v) for k, v in old_values.items()}
            for field in sorted(set(cur_values) | set(old_values)):
                if cur_values.get(field) != old_values.get(field):
                    changes.append({"path": f"containers.{container}.{key}.{field}", "previous": old_values.get(field), "current": cur_values.get(field)})
    return {"status": "changed" if changes else "unchanged", "changes": changes, "current_revision": payload.get("current_revision")}


def collect_workload_deployment(service: str, namespace: str) -> dict | None:
    """Read the current workload deployment for identity correlation."""
    try:
        response = http_requests.get(
            f"{EXECUTOR_URL}/tools/deployment",
            params={"service": service, "namespace": namespace},
            headers={"Authorization": f"Bearer {EXECUTOR_TOKEN}"},
            timeout=10,
        )
        payload = response.json() if response.ok else {}
        deployment = payload.get("deployment")
        return deployment if isinstance(deployment, dict) else None
    except Exception:
        return None


def collect_gitops_deployed_change(
    service: str,
    namespace: str,
    template_diff: dict | None,
    gitops_client,
) -> dict:
    """Find the synced GitOps commit that describes the current deployed diff."""
    if not template_diff:
        return {"status": "unavailable", "reason": "no_deployed_configuration_diff"}
    try:
        response = http_requests.get(
            f"{EXECUTOR_URL}/tools/argocd-sync",
            params={"app": _argocd_app_name(service, namespace)},
            headers={"Authorization": f"Bearer {EXECUTOR_TOKEN}"},
            timeout=10,
        )
        sync = response.json() if response.ok else {}
    except Exception:
        return {"status": "unavailable", "reason": "argocd_unavailable"}
    if sync.get("sync_status") != "Synced":
        return {"status": "unavailable", "reason": "gitops_not_synced"}
    revision = sync.get("raw", {}).get("status", {}).get("sync", {}).get("revision", "")
    if not revision:
        return {"status": "unavailable", "reason": "gitops_revision_unavailable"}

    history = gitops_client.list_path_commits(
        f"apps/{_short_name(service)}/overlays/{_env_name(namespace)}",
        revision,
    )
    if history.status != "available" or not isinstance(history.value, list):
        return {"status": "unavailable", "reason": history.reason or "gitops_history_unavailable"}
    details = []
    for item in history.value[:20]:
        sha = str(item.get("sha", ""))
        if not sha:
            continue
        detail = gitops_client.get_commit(sha)
        if detail.status == "available" and isinstance(detail.value, dict):
            details.append(detail.value)
    selected = select_gitops_change(details, template_diff)
    if selected.get("status") == "found":
        selected["service"] = service
        selected["synced_revision"] = revision
    return selected


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


def _gitops_file_path(service: str, namespace: str, template_diff: dict | None = None) -> str:
    short = _short_name(service)
    if namespace == "platform":
        return f"platform/{short}/deployment.yaml"
    if template_diff and template_diff.get("image_changed"):
        return f"apps/{short}/overlays/{_env_name(namespace)}/kustomization.yaml"
    return f"apps/{short}/base/deployment.yaml"


def _github_headers() -> dict:
    return {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": f"application/vnd.github+json"}


def _github_get_raw_file(path: str, ref: str = "main") -> str:
    try:
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.raw",
        }
        r = http_requests.get(
            f"{GITHUB_API_URL}/repos/{GITHUB_GITOPS_REPO}/contents/{path}",
            params={"ref": ref},
            headers=headers,
            timeout=10
        )
        return r.text if r.ok else ""
    except Exception:
        return ""


def _unknown_event_contract_version(message: str) -> str:
    match = re.search(
        r"unknown event type\s+[\"']?[^\"']*\.([a-z][a-z0-9_-]*)[\"']?",
        message or "",
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


def _commit_provenance(detail: dict, file_path: str) -> dict:
    commit = detail.get("commit") or {}
    author = commit.get("author") or {}
    diff_snippet = next(
        (item.get("patch", "") for item in detail.get("files", [])
         if item.get("filename") == file_path),
        "",
    )
    return {
        "classification": "gitops-commit",
        "commit": {
            "sha": str(detail.get("sha", ""))[:7],
            "author": author.get("name", ""),
            "message": str(commit.get("message", "")).split("\n")[0],
            "date": author.get("date", ""),
            "url": detail.get("html_url", ""),
            "diff_snippet": diff_snippet,
        },
        "pr": None,
        "file_path": file_path,
    }


def _literal_env_value(yaml_text: str, name: str) -> str:
    match = re.search(
        rf"(?:^|\n)\s*-\s*name:\s*[\"']?{re.escape(name)}[\"']?\s*\r?\n"
        r"\s*value:\s*([^\s#]+)",
        yaml_text or "",
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return match.group(1).strip("\"'")


def is_contract_field(name: str) -> bool:
    normalized = str(name or "").upper()
    return (
        "CONTRACT" in normalized
        or "SCHEMA" in normalized
        or ("VERSION" in normalized
            and any(marker in normalized for marker in _VERSIONED_MESSAGE_FIELD_MARKERS))
    )


def _added_literal_env_entries(patch: str) -> list[tuple[str, str]]:
    entries = []
    current_field = ""
    for raw_line in (patch or "").splitlines():
        if raw_line.startswith("@@"):
            current_field = ""
            continue
        prefix = raw_line[0] if raw_line[:1] in {" ", "+", "-"} else " "
        content = raw_line[1:] if raw_line[:1] in {" ", "+", "-"} else raw_line
        name_match = re.match(r"\s*-\s*name:\s*([^\s#]+)", content, flags=re.IGNORECASE)
        if name_match:
            current_field = name_match.group(1).strip("\"'")
            continue
        value_match = re.match(r"\s*value:\s*([^\s#]+)", content, flags=re.IGNORECASE)
        if prefix == "+" and current_field and value_match:
            entries.append((current_field, value_match.group(1).strip("\"'")))
    return entries


def collect_gitops_change_evidence(service: str, namespace: str,
                                    failure_message: str) -> dict | None:
    """Find durable GitOps evidence for an exact rejected event-contract version.

    ReplicaSet history is intentionally short-lived. This lookup walks the synced
    overlay's recent commits and accepts only a patch that names both the contract
    field and the version found in the canonical failure log.
    """
    version = _unknown_event_contract_version(failure_message)
    if not version or namespace == "platform":
        return None

    try:
        r = http_requests.get(
            f"{EXECUTOR_URL}/tools/argocd-sync",
            params={"app": _argocd_app_name(service, namespace)},
            headers={"Authorization": f"Bearer {EXECUTOR_TOKEN}"},
            timeout=10,
        )
        data = r.json() if r.ok else {}
        if data.get("sync_status") != "Synced":
            return None
        synced_sha = data.get("raw", {}).get("status", {}).get("sync", {}).get("revision", "")
        if not synced_sha:
            return None

        short = _short_name(service)
        environment = _env_name(namespace)
        history_path = f"apps/{short}/overlays/{environment}"
        r = http_requests.get(
            f"{GITHUB_API_URL}/repos/{GITHUB_GITOPS_REPO}/commits",
            params={"path": history_path, "sha": synced_sha, "per_page": 20},
            headers=_github_headers(),
            timeout=10,
        )
        commits = r.json() if r.ok else []

        allowed_prefixes = (
            f"apps/{short}/base/",
            f"apps/{short}/overlays/{environment}/",
        )
        for item in commits:
            sha = item.get("sha", "")
            if not sha:
                continue
            r = http_requests.get(
                f"{GITHUB_API_URL}/repos/{GITHUB_GITOPS_REPO}/commits/{sha}",
                headers=_github_headers(),
                timeout=10,
            )
            detail = r.json() if r.ok else {}
            matched_file = ""
            matched_field = ""
            for changed_file in detail.get("files", []):
                filename = changed_file.get("filename", "")
                if not filename.startswith(allowed_prefixes):
                    continue
                for field, value in _added_literal_env_entries(changed_file.get("patch", "")):
                    if is_contract_field(field) and value.lower() == version.lower():
                        matched_file = filename
                        matched_field = field
                        break
                if matched_file:
                    break
            if not matched_file:
                continue

            provenance = _commit_provenance(detail, matched_file)
            try:
                r = http_requests.get(
                    f"{GITHUB_API_URL}/repos/{GITHUB_GITOPS_REPO}/commits/{sha}/pulls",
                    headers={**_github_headers(), "Accept": "application/vnd.github.groot-preview+json"},
                    timeout=10,
                )
                prs = r.json() if r.ok else []
                if prs:
                    provenance["pr"] = {
                        "number": prs[0]["number"],
                        "title": prs[0]["title"],
                        "url": prs[0]["html_url"],
                    }
            except Exception:
                pass

            changed_at = provenance["commit"].get("date", "")
            base_yaml = _github_get_raw_file(
                f"apps/{short}/base/deployment.yaml", synced_sha,
            )
            previous_value = _literal_env_value(
                base_yaml, matched_field,
            )
            if previous_value.lower() == version.lower():
                previous_value = ""
            return {
                "service": service,
                "source": "gitops_history",
                "image_changed": False,
                "old_image": "",
                "new_image": "",
                "env_changed": True,
                "env_diff": [{
                    "key": matched_field,
                    "old_value": previous_value,
                    "new_value": version,
                }],
                "changed_at": changed_at,
                "file_path": matched_file,
                "provenance": provenance,
            }
    except Exception:
        return None
    return None


def _parse_yaml_deployment(yaml_str: str) -> dict:
    import textwrap
    yaml_str = textwrap.dedent(yaml_str)
    res = {"spec": {"replicas": 1, "template": {"spec": {"containers": [{"image": "", "env": []}]}}}}
    lines = yaml_str.split("\n")
    in_spec = False
    in_template = False
    in_containers = False
    in_env = False

    spec_indent = -1
    template_indent = -1
    containers_indent = -1
    env_indent = -1

    current_env = {}

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(stripped)

        if in_env and (indent < env_indent or (indent == env_indent and not stripped.startswith("-"))):
            in_env = False
            env_indent = -1
            if current_env:
                res["spec"]["template"]["spec"]["containers"][0]["env"].append(current_env)
                current_env = {}

        if in_containers and (indent < containers_indent or (indent == containers_indent and not stripped.startswith("-"))):
            in_containers = False
            containers_indent = -1

        if in_template and indent <= template_indent:
            in_template = False
            template_indent = -1

        if in_spec and indent <= spec_indent:
            in_spec = False
            spec_indent = -1

        if stripped.startswith("spec:"):
            if not in_spec:
                in_spec = True
                spec_indent = indent
        elif stripped.startswith("replicas:") and in_spec and not in_template:
            val = stripped.split(":", 1)[1].strip()
            try:
                res["spec"]["replicas"] = int(val)
            except Exception:
                pass
        elif stripped.startswith("template:") and in_spec:
            in_template = True
            template_indent = indent
        elif stripped.startswith("containers:") and in_template:
            in_containers = True
            containers_indent = indent
        elif (stripped.startswith("- image:") or stripped.startswith("image:")) and in_containers:
            img = stripped.split(":", 1)[1].strip()
            if (img.startswith('"') and img.endswith('"')) or (img.startswith("'") and img.endswith("'")):
                img = img[1:-1]
            res["spec"]["template"]["spec"]["containers"][0]["image"] = img
        elif stripped.startswith("env:") and in_containers:
            in_env = True
            env_indent = indent
        elif stripped.startswith("- name:") and in_env:
            if current_env:
                res["spec"]["template"]["spec"]["containers"][0]["env"].append(current_env)
            name = stripped.split(":", 1)[1].strip()
            if (name.startswith('"') and name.endswith('"')) or (name.startswith("'") and name.endswith("'")):
                name = name[1:-1]
            current_env = {"name": name, "value": ""}
        elif stripped.startswith("value:") and in_env:
            val = stripped.split(":", 1)[1].strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            if current_env:
                current_env["value"] = val

    if current_env:
        res["spec"]["template"]["spec"]["containers"][0]["env"].append(current_env)

    return res


def _compute_drift(live_deploy: dict, desired_yaml_str: str) -> list:
    desired = _parse_yaml_deployment(desired_yaml_str)
    live_spec = live_deploy.get("spec", {})
    desired_spec = desired.get("spec", {})

    diffs = []

    # Replicas
    live_rep = live_spec.get("replicas", 1)
    desired_rep = desired_spec.get("replicas", 1)
    if live_rep != desired_rep:
        diffs.append({
            "key": "replicas",
            "correct": str(desired_rep),
            "wrong": str(live_rep)
        })

    # Image
    def _first_img(spec):
        try:
            return spec.get("template", {}).get("spec", {}).get("containers", [])[0].get("image", "")
        except Exception:
            return ""
    live_img = _first_img(live_spec)
    desired_img = _first_img(desired_spec)
    if live_img and desired_img and live_img != desired_img:
        diffs.append({
            "key": "image",
            "correct": desired_img,
            "wrong": live_img
        })

    # Env
    def _env_map(spec):
        try:
            containers = spec.get("template", {}).get("spec", {}).get("containers", [])
            if not containers:
                return {}
            return {e.get("name"): e.get("value", "") for e in containers[0].get("env", []) if e.get("name")}
        except Exception:
            return {}
    live_env = _env_map(live_spec)
    desired_env = _env_map(desired_spec)

    for k, v in desired_env.items():
        if live_env.get(k) != v:
            diffs.append({
                "key": f"env.{k}",
                "correct": v,
                "wrong": live_env.get(k, "")
            })
    for k, v in live_env.items():
        if k not in desired_env:
            diffs.append({
                "key": f"env.{k}",
                "correct": "(none)",
                "wrong": v
            })

    return diffs


def _fetch_git_provenance(file_path: str, synced_sha: str) -> dict:
    if not synced_sha:
        return {"classification": "gitops-commit", "commit": None, "pr": None}

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
        "message":      (detail.get("commit") or {}).get("message", "").split("\n")[0],
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


def collect_provenance(service: str, namespace: str, template_diff: dict | None, dependency: dict | None = None) -> dict | None:
    # 1. Check if dependency is unhealthy and handle dependency provenance
    if dependency and (
        dependency.get("pods_desired") == 0 or 
        dependency.get("pods_available") != dependency.get("pods_desired") or 
        dependency.get("waiting_reason")
    ):
        dep_name = dependency["name"]
        dep_ns = dependency["namespace"]
        try:
            r = http_requests.get(
                f"{EXECUTOR_URL}/tools/deployment",
                params={"service": dep_name, "namespace": dep_ns},
                headers={"Authorization": f"Bearer {EXECUTOR_TOKEN}"},
                timeout=10,
            )
            live_deploy = r.json().get("deployment") if r.ok else None
        except Exception:
            live_deploy = None

        if live_deploy:
            tracking_id = live_deploy.get("metadata", {}).get("annotations", {}).get("argocd.argoproj.io/tracking-id", "")
            if tracking_id and ":" in tracking_id:
                app_name = tracking_id.split(":")[0]
            else:
                app_name = "vroom-infrastructure" if dep_ns == "platform" else f"vroom-{_env_name(dep_ns)}-{_short_name(dep_name)}"

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
                synced_sha = raw_app.get("status", {}).get("sync", {}).get("revision", "HEAD")
            except Exception:
                sync_status = "Unknown"
                synced_sha = "HEAD"

            file_path = _gitops_file_path(dep_name, dep_ns)

            if sync_status != "Synced":
                desired_yaml = _github_get_raw_file(file_path, synced_sha or "HEAD")
                drift = _compute_drift(live_deploy, desired_yaml) if desired_yaml else [{"key": "configuration", "correct": "GitOps config", "wrong": "drift detected"}]
                diff_str = ", ".join(f"{x['key']}: {x['correct']} ➔ {x['wrong']}" for x in drift)
                
                changed_at = ""
                if live_deploy:
                    for cond in live_deploy.get("status", {}).get("conditions", []):
                        if cond.get("type") == "Progressing" and cond.get("status") == "True":
                            changed_at = cond.get("lastUpdateTime", "")
                            break

                return {
                    "classification": "hotfix",
                    "target": "dependency",
                    "dependency_name": f"{dep_ns}/{dep_name}",
                    "diff": diff_str,
                    "drift": drift,
                    "changed_at": changed_at
                }
            else:
                res = _fetch_git_provenance(file_path, synced_sha)
                res["target"] = "dependency"
                res["dependency_name"] = f"{dep_ns}/{dep_name}"
                return res

    # 2. Main service provenance logic (fallback/default)
    if template_diff is None:
        return None

    if template_diff.get("provenance"):
        return dict(template_diff["provenance"])

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

    file_path = _gitops_file_path(service, namespace, template_diff)
    return _fetch_git_provenance(file_path, synced_sha)


def classify_provenance(provenance: dict | None, changed_at: str, service: str,
                        drift: bool | list | None = False,
                        failure_predates: bool = False) -> dict:
    """Classify whether a change is causal context for the selected incident.

    Timing alone is insufficient: the candidate must identify the affected service,
    be inside the bounded alert-relative window, and have no drift or ordering
    contradiction.
    """
    result = {
        "status": "unavailable", "reason_codes": [], "changed_at": changed_at,
        "commit_sha": None, "file_path": provenance.get("file_path") if provenance else None,
        "affected_fields": provenance.get("affected_fields", []) if provenance else [],
    }
    if not provenance or not changed_at:
        result["reason_codes"].append("provenance_unavailable")
        return result
    commit = provenance.get("commit") or {}
    result["commit_sha"] = commit.get("sha") or provenance.get("commit_sha")
    if drift:
        result["status"] = "conflicting"
        result["reason_codes"].append("live_desired_drift")
        return result
    if failure_predates:
        result["status"] = "conflicting"
        result["reason_codes"].append("failure_predates_change")
        return result
    if provenance.get("service") and provenance.get("service") != service:
        result["status"] = "recent_context"
        result["reason_codes"].append("different_service")
        return result
    try:
        changed = datetime.fromisoformat(changed_at.replace("Z", "+00:00")).astimezone(timezone.utc)
        started_raw = provenance.get("alert_started_at")
        started = datetime.fromisoformat(started_raw.replace("Z", "+00:00")).astimezone(timezone.utc) if started_raw else None
    except (TypeError, ValueError):
        result["reason_codes"].append("invalid_change_timestamp")
        return result
    if started is None:
        result["status"] = "recent_context"
        result["reason_codes"].append("alert_window_unavailable")
        return result
    age_seconds = (started - changed).total_seconds()
    if age_seconds < 0 or age_seconds > 15 * 60:
        result["status"] = "recent_context"
        result["reason_codes"].append("outside_alert_window")
        return result
    result["status"] = "causal_candidate"
    result["reason_codes"].append("same_service_change_precedes_failure")
    return result


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
    pods_running = int(_prom_scalar(
        f'kube_deployment_status_replicas{{deployment="{service}",namespace="{namespace}"}}'
    ))
    pods_ready = int(_prom_scalar(
        f'kube_deployment_status_replicas_ready{{deployment="{service}",namespace="{namespace}"}}'
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
        "pods_running":               pods_running,
        "pods_ready":                 pods_ready,
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
