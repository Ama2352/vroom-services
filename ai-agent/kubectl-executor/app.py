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

_NS_RE = re.compile(r'^[\w][\w-]*$')
_POD_RE = re.compile(r'^[\w][\w.\-]*$')
_INT_RE = re.compile(r'^\d+$')


def _auth(req):
    return req.headers.get("Authorization", "") == f"Bearer {BEARER_TOKEN}"


def _run(cmd: list[str]) -> tuple[dict, int]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return {"stdout": result.stdout[:2000], "stderr": result.stderr[:500], "returncode": result.returncode}, 200
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
    if not _NS_RE.match(ns):
        return jsonify({"error": "Invalid namespace"}), 400
    body, status = _run(["kubectl", "get", "pods", "-n", ns])
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
    body, status = _run(["kubectl", "logs", "-n", ns, "-l", f"app={service}", f"--tail={tail}"])
    return jsonify(body), status


@app.route("/tools/events")
def tool_events():
    if not _auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    ns = request.args.get("namespace", "").strip()
    if not _NS_RE.match(ns):
        return jsonify({"error": "Invalid namespace"}), 400
    body, status = _run(["kubectl", "get", "events", "-n", ns])
    return jsonify(body), status


@app.route("/tools/describe")
def tool_describe():
    if not _auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    pod = request.args.get("pod", "").strip()
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

@app.route("/tools/traces")
def tool_traces():
    if not _auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    service = request.args.get("service", "").strip()
    error_only = request.args.get("error_only", "true").lower() == "true"
    if not _NS_RE.match(service):
        return jsonify({"error": "Invalid service"}), 400
    now = int(time.time())
    tags = f"service.name={service}"
    if error_only:
        tags += "&error=true"
    params = {
        "tags": tags,
        "start": f"{now - 900}000000000",
        "end": f"{now}000000000",
        "limit": "5",
    }
    try:
        r = http_requests.get(f"{TEMPO_URL}/api/search", params=params, timeout=2)
        if r.status_code == 200:
            traces = r.json().get("traces", [])
            if not traces:
                return jsonify({"stdout": "No errored traces found in last 15 minutes.", "returncode": 0})
            lines = [
                f"trace_id={t.get('traceID','')} root={t.get('rootTraceName','')} duration={t.get('durationMs','')}ms"
                for t in traces[:5]
            ]
            return jsonify({"stdout": "\n".join(lines), "returncode": 0})
        return jsonify({"stdout": "[traces unavailable]", "returncode": 1})
    except Exception as e:
        return jsonify({"stdout": f"[traces unavailable: {e}]", "returncode": 1})


# ── Write tool (requires human approval upstream in n8n) ─────────────────────

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
