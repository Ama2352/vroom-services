from flask import Flask, request, jsonify
import subprocess, os, re, time
import requests as http_requests

app = Flask(__name__)

ALLOWLIST_PATTERNS = [
    r"^kubectl get pods( -n \w[\w-]*)?$",
    r"^kubectl describe pod [\w][\w-]* -n \w[\w-]*$",
    r"^kubectl logs [\w][\w-]* -n \w[\w-]*( --tail=\d+)?( \| grep( -[iv])? \S+)?$",
    r"^kubectl logs -n \w[\w-]* -l \w[\w-]*=\w[\w-]*( --since=\d+[smh])?( --tail=\d+)?( \| grep( -[iv])? \S+)?$",
    r"^kubectl top pods( -n \w[\w-]*)?$",
    r"^kubectl get events -n \w[\w-]*$",
    r"^kubectl rollout status deployment/[\w][\w-]* -n \w[\w-]*$",
    r"^kubectl get deployments( -n \w[\w-]*)?$",
    r"^kubectl get services( -n \w[\w-]*)?$",
    r"^kubectl get nodes$",
]

BEARER_TOKEN = os.environ.get("EXECUTOR_API_KEY", "change-me")
TEMPO_URL = os.environ.get("TEMPO_URL", "http://tempo.monitoring.svc.cluster.local:3100")

_NS_RE    = re.compile(r'^[\w][\w-]*$')
_POD_RE   = re.compile(r'^[\w][\w.\-]*$')
_INT_RE   = re.compile(r'^\d+$')
_LABEL_RE = re.compile(r'^[\w][\w./-]*=[\w][\w.-]*$')


def _auth(req):
    return req.headers.get("Authorization", "") == f"Bearer {BEARER_TOKEN}"


def _run(cmd: list[str]) -> tuple[dict, int]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        stdout = result.stdout
        if len(stdout) > 2000:
            cut = stdout[:2000].rfind('\n')
            stdout = stdout[:cut] if cut > 0 else stdout[:2000]
        return {"stdout": stdout, "stderr": result.stderr[:500], "returncode": result.returncode}, 200
    except subprocess.TimeoutExpired:
        return {"error": "kubectl timed out after 30s", "stdout": "", "returncode": -1}, 500
    except FileNotFoundError as e:
        return {"error": f"Executable not found: {e}", "stdout": "", "returncode": -1}, 500
    except Exception as e:
        return {"error": str(e), "stdout": "", "returncode": -1}, 500


# ── Legacy /exec endpoint (kept for backward compatibility) ──────────────────

def is_allowed(command: str) -> bool:
    return any(re.match(p, command.strip()) for p in ALLOWLIST_PATTERNS)


@app.route("/exec", methods=["POST"])
def execute():
    if not _auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    command = data.get("command", "").strip()
    if not is_allowed(command):
        return jsonify({"error": f"Command not in allowlist: {command}", "stdout": ""}), 400
    exec_command = command.split(' | ')[0].strip()
    body, status = _run(exec_command.split())
    return jsonify(body), status


# ── Named read tools ─────────────────────────────────────────────────────────

@app.route("/tools/pods")
def tool_pods():
    if not _auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    ns = request.args.get("namespace", "").strip()
    label_selector = request.args.get("label_selector", "").strip()
    if not _NS_RE.match(ns):
        return jsonify({"error": "Invalid namespace"}), 400
    cmd = ["kubectl", "get", "pods", "-n", ns]
    if label_selector:
        if not _LABEL_RE.match(label_selector):
            return jsonify({"error": "Invalid label_selector"}), 400
        cmd.extend(["-l", label_selector])
    body, status = _run(cmd)
    return jsonify(body), status


@app.route("/tools/logs")
def tool_logs():
    if not _auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    service = request.args.get("service", "").strip()
    ns = request.args.get("namespace", "").strip()
    tail = request.args.get("tail", "50").strip()
    if not _NS_RE.match(service) or not _NS_RE.match(ns):
        return jsonify({"error": "Invalid service or namespace"}), 400
    if not _INT_RE.match(tail) or int(tail) > 500:
        return jsonify({"error": "Invalid tail (must be 1-500)"}), 400

    # Prefer --previous logs from a crashlooping pod — those contain the actual crash reason
    pods_body, _ = _run(["kubectl", "get", "pods", "-n", ns, "-l", f"app={service}", "--no-headers"])
    for line in pods_body.get("stdout", "").splitlines():
        if "CrashLoopBackOff" in line:
            parts = line.split()
            if parts and _POD_RE.match(parts[0]):
                prev, prev_status = _run(
                    ["kubectl", "logs", parts[0], "-n", ns, f"--tail={tail}", "--previous"]
                )
                if prev.get("stdout", "").strip():
                    return jsonify(prev), prev_status
                break

    body, status = _run(["kubectl", "logs", "-n", ns, "-l", f"app={service}", f"--tail={tail}"])
    return jsonify(body), status


@app.route("/tools/events")
def tool_events():
    if not _auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    ns      = request.args.get("namespace", "").strip()
    service = request.args.get("service",   "").strip()
    if not _NS_RE.match(ns):
        return jsonify({"error": "Invalid namespace"}), 400
    body, status = _run([
        "kubectl", "get", "events", "-n", ns,
        "--sort-by=.lastTimestamp",
    ])
    _LIFECYCLE_NOISE = {"Scheduled", "Pulling", "Pulled", "Created", "Started"}
    if service and body.get("stdout"):
        lines  = body["stdout"].splitlines()
        header = lines[0] if lines else ""
        filtered = [
            l for l in lines[1:]
            if service in l and not any(reason in l for reason in _LIFECYCLE_NOISE)
        ]
        body["stdout"] = "\n".join([header] + filtered) if filtered else f"{header}\n(no notable events for {service})"
    return jsonify(body), status


@app.route("/tools/describe")
def tool_describe():
    if not _auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    # Accept "name" (tool-calling schema) or legacy "pod" param
    pod = (request.args.get("name") or request.args.get("pod", "")).strip()
    ns = request.args.get("namespace", "").strip()
    if not _POD_RE.match(pod) or not _NS_RE.match(ns):
        return jsonify({"error": "Invalid pod or namespace"}), 400
    body, status = _run(["kubectl", "describe", "pod", pod, "-n", ns])
    return jsonify(body), status


@app.route("/tools/metrics")
def tool_metrics():
    if not _auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    ns = request.args.get("namespace", "").strip()
    if not _NS_RE.match(ns):
        return jsonify({"error": "Invalid namespace"}), 400
    body, status = _run(["kubectl", "top", "pods", "-n", ns])
    return jsonify(body), status


# ── Tempo traces (best-effort read) ─────────────────────────────────────────

def _fetch_error_span(trace_id: str) -> str:
    """Fetch full Jaeger span tree for a trace; return leaf error span detail or ''."""
    try:
        r = http_requests.get(f"{TEMPO_URL}/api/traces/{trace_id}", timeout=7)
        if r.status_code != 200:
            return ""
        data = r.json().get("data", [])
        if not data:
            return ""
        spans = data[0].get("spans", [])

        span_by_id = {s["spanID"]: s for s in spans}

        def is_error(span):
            tags = {t["key"]: t.get("value") for t in span.get("tags", [])}
            return tags.get("error") is True or tags.get("otel.status_code") == "ERROR"

        error_spans = [s for s in spans if is_error(s)]
        if not error_spans:
            return ""

        # Leaf = error span whose children are not also errors
        child_ids   = {s.get("parentSpanID") for s in error_spans}
        leaf_errors = [s for s in error_spans if s["spanID"] not in child_ids]
        target      = leaf_errors[0] if leaf_errors else error_spans[0]

        tags    = {t["key"]: t.get("value") for t in target.get("tags", [])}
        err_msg = tags.get("error.message") or tags.get("exception.message") or "error"
        svc     = target.get("process", {}).get("serviceName", "?")
        op      = target.get("operationName", "?")

        parent_id  = target.get("parentSpanID", "")
        parent_svc = ""
        if parent_id and parent_id in span_by_id:
            parent_svc = span_by_id[parent_id].get("process", {}).get("serviceName", "")

        detail = f"  error span: {svc} → {op}\n  error: \"{err_msg}\""
        if parent_svc and parent_svc != svc:
            detail += f"\n  parent: {parent_svc} (OK)"
        return detail
    except Exception as e:
        print(f"[traces] span fetch failed (non-fatal): {e}", flush=True)
        return ""


@app.route("/tools/traces")
def tool_traces():
    if not _auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    service = request.args.get("service", "").strip()
    if not _NS_RE.match(service):
        return jsonify({"error": "Invalid service"}), 400
    now = int(time.time())
    # Tempo 1.7.x /api/search: start/end are Unix epoch seconds (not nanoseconds).
    # Only resource-attribute tags (service.name) are reliably indexed in 1.7.x.
    params = {
        "tags":  f"service.name={service}",
        "start": str(now - 900),
        "end":   str(now),
        "limit": "5",
    }
    try:
        r = http_requests.get(f"{TEMPO_URL}/api/search", params=params, timeout=8)
        if r.status_code != 200:
            print(f"[traces] Tempo returned HTTP {r.status_code}: {r.text[:300]}", flush=True)
            return jsonify({"stdout": "[traces unavailable]", "returncode": 1})

        traces = r.json().get("traces", [])
        if not traces:
            return jsonify({"stdout": "No errored traces found in last 15 minutes.", "returncode": 0})

        lines = [
            f"trace_id={t.get('traceID','')} root={t.get('rootTraceName','')} duration={t.get('durationMs','')}ms"
            for t in traces[:5]
        ]

        first_id = traces[0].get("traceID", "")
        if first_id:
            detail = _fetch_error_span(first_id)
            if detail:
                lines[0] += f"\n{detail}"

        return jsonify({"stdout": "\n".join(lines), "returncode": 0})
    except Exception as e:
        print(f"[traces] exception: {e}", flush=True)
        return jsonify({"stdout": f"[traces unavailable: {e}]", "returncode": 1})


# ── Write tools (require human approval upstream in n8n) ─────────────────────

@app.route("/tools/scale", methods=["POST"])
def tool_scale():
    if not _auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    deployment = data.get("deployment", "").strip()
    ns = data.get("namespace", "").strip()
    replicas = str(data.get("replicas", 1))
    if not _NS_RE.match(deployment) or not _NS_RE.match(ns):
        return jsonify({"error": "Invalid deployment or namespace"}), 400
    if not _INT_RE.match(replicas) or int(replicas) > 10:
        return jsonify({"error": "Invalid replicas (must be 0-10)"}), 400
    body, status = _run(["kubectl", "scale", f"deployment/{deployment}", "-n", ns, f"--replicas={replicas}"])
    return jsonify(body), status


@app.route("/tools/restart", methods=["POST"])
def tool_restart():
    if not _auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    deployment = data.get("deployment", "").strip()
    ns = data.get("namespace", "").strip()
    if not _NS_RE.match(deployment) or not _NS_RE.match(ns):
        return jsonify({"error": "Invalid deployment or namespace"}), 400
    body, status = _run(["kubectl", "rollout", "restart", f"deployment/{deployment}", "-n", ns])
    return jsonify(body), status


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
