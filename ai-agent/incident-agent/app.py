import os, json, uuid, threading
import redis as redis_lib
import requests
from flask import Flask, request, jsonify

from memory import store_incident, search_memory as memory_search, connect as redis_connect
from collector import collect_bundle
from react_loop import run_react_loop, DEFAULT_MODELS
from tools import call_tool
from seed import seed_if_empty

app = Flask(__name__)

REDIS_URL       = os.environ.get("REDIS_URL", "redis://redis.platform.svc.cluster.local:6379")
OPENROUTER_KEY  = os.environ.get("OPENROUTER_API_KEY", "")
EXECUTOR_URL    = os.environ.get("KUBECTL_EXECUTOR_URL", "http://kubectl-executor.monitoring.svc.cluster.local:5001")
EXECUTOR_TOKEN  = os.environ.get("EXECUTOR_API_KEY", "change-me")
PENDING_TTL     = 3600  # seconds — matches n8n Wait node timeout

rdb = redis_connect(REDIS_URL)

_MODELS_KEY = "config:models"


def _load_models(rdb) -> list:
    raw = rdb.get(_MODELS_KEY)
    if raw:
        return json.loads(raw)
    rdb.set(_MODELS_KEY, json.dumps(DEFAULT_MODELS))
    return list(DEFAULT_MODELS)


_current_models: list = _load_models(rdb)


def _background_seed():
    try:
        n = seed_if_empty(rdb)
        print(f"[seed] seeded {n} incidents from vroom-ops.md")
    except Exception as e:
        print(f"[seed] cold-start seed failed: {e}")

threading.Thread(target=_background_seed, daemon=True).start()


def _extract_evidence(steps: list) -> str:
    priority = ["get_traces", "get_events", "get_logs", "describe_pod", "get_pods"]
    _skip = {"[no output]", "No errored traces found in last 15 minutes."}
    by_tool = {}
    for step in steps:
        obs = step.get("observation", "")
        if not obs or obs.startswith("[tool") or obs in _skip:
            continue
        for tool in priority:
            if step["action"].startswith(tool) and tool not in by_tool:
                by_tool[tool] = obs
    for tool in priority:
        if tool in by_tool:
            lines = [l for l in by_tool[tool].splitlines() if l.strip()][:8]
            return f"[{tool}]\n" + "\n".join(lines)
    return ""


def _suggested_command(rem: dict) -> str:
    if not rem:
        return ""
    t = rem.get("tool", "")
    a = rem.get("args", {})
    dep, ns = a.get("deployment", ""), a.get("namespace", "")
    if t == "scale_deployment":
        return f"kubectl scale deployment/{dep} -n {ns} --replicas={a.get('replicas', 1)}"
    if t == "restart_deployment":
        return f"kubectl rollout restart deployment/{dep} -n {ns}"
    return ""


def _dispatch_tool(tool_name: str, args: dict) -> str:
    if tool_name == "search_memory":
        query = args.get("query", "")
        return memory_search(rdb, query)
    return call_tool(tool_name, args)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "incidents_in_memory": rdb.scard("incidents:index")})


@app.route("/memory/search")
def memory_search_endpoint():
    query = request.args.get("q", "").strip()
    limit = int(request.args.get("limit", "3"))
    if not query:
        return jsonify({"result": "no relevant memory found"})
    result = memory_search(rdb, query, limit=limit)
    return jsonify({"result": result})


@app.route("/admin/runbook")
def admin_runbook():
    path = os.path.join(os.environ.get("DOCS_DIR", "/docs"), "vroom-ops.md")
    try:
        return app.response_class(open(path).read(), mimetype="text/plain")
    except FileNotFoundError:
        return jsonify({"error": "runbook not found at " + path}), 404


@app.route("/admin/models", methods=["GET"])
def get_models():
    return jsonify({"models": _current_models})


@app.route("/admin/models", methods=["POST"])
def set_models():
    global _current_models
    data = request.get_json(silent=True)
    if not isinstance(data, list) or not data or not all(isinstance(m, str) for m in data):
        return jsonify({"error": "body must be a non-empty JSON array of strings"}), 400
    _current_models[:] = data
    rdb.set(_MODELS_KEY, json.dumps(data))
    return jsonify({"models": _current_models})


@app.route("/investigate", methods=["POST"])
def investigate():
    data = request.get_json(silent=True) or {}
    alert_name = data.get("alert_name", "UnknownAlert")
    service    = data.get("service", "unknown")
    namespace  = data.get("namespace", "vroom-dev")
    severity   = data.get("severity", "warning")

    seed_if_empty(rdb)  # self-heal if Redis restarted between incident-agent startups

    bundle = collect_bundle(service, namespace)
    alert  = {"alert_name": alert_name, "service": service, "namespace": namespace, "bundle": bundle}

    diagnosis = run_react_loop(alert, _dispatch_tool, OPENROUTER_KEY, models=_current_models)

    eid = str(uuid.uuid4())
    rdb.setex(f"pending:{eid}", PENDING_TTL, json.dumps({"alert": alert, "diagnosis": diagnosis}))

    rem      = diagnosis.get("remediation")
    steps    = diagnosis.get("investigation_steps", [])
    evidence = _extract_evidence(steps)
    cmd      = _suggested_command(rem)

    return jsonify({
        "execution_id":        eid,
        "service":             service,
        "alert_name":          alert_name,
        "root_cause":          diagnosis["root_cause"],
        "confidence":          diagnosis["confidence"],
        "investigation_steps": len(steps),
        "remediation":         rem,
        "evidence_snippet":    evidence,
        "suggested_command":   cmd,
    })


@app.route("/remediate", methods=["POST"])
def remediate():
    data    = request.get_json(silent=True) or {}
    eid     = data.get("execution_id", "")
    approved = data.get("approved", False)

    raw = rdb.getdel(f"pending:{eid}")
    if raw is None:
        return jsonify({"error": "Unknown or expired execution_id"}), 404

    pending   = json.loads(raw)
    diagnosis = pending["diagnosis"]
    alert     = pending["alert"]
    rem       = diagnosis.get("remediation")

    if not approved or not rem:
        return jsonify({"outcome": "skipped", "stdout": "", "interpretation": "Operator declined or no remediation proposed."})

    headers = {"Authorization": f"Bearer {EXECUTOR_TOKEN}", "Content-Type": "application/json"}
    endpoint = "/tools/scale" if rem.get("tool") == "scale_deployment" else "/tools/restart"
    r = requests.post(f"{EXECUTOR_URL}{endpoint}", json=rem.get("args", {}), headers=headers, timeout=35)
    stdout = r.json().get("stdout", "") if r.status_code == 200 else f"[executor error: HTTP {r.status_code}]"
    outcome = "resolved" if r.status_code == 200 else "escalated"

    interpretation = _interpret(rem, stdout)

    store_incident(rdb, {
        "alert_name":          alert["alert_name"],
        "service":             alert["service"],
        "namespace":           alert["namespace"],
        "symptoms":            alert["bundle"],
        "investigation_steps": diagnosis.get("investigation_steps", []),
        "root_cause":          diagnosis["root_cause"],
        "remediation_tool":    rem["tool"],
        "remediation_args":    rem.get("args", {}),
        "outcome":             outcome,
    })

    return jsonify({"outcome": outcome, "stdout": stdout, "interpretation": interpretation})


def _interpret(remediation: dict, stdout: str) -> str:
    if not OPENROUTER_KEY:
        return stdout[:200]
    cmd = _suggested_command(remediation) or f"kubectl rollout restart deployment/{remediation.get('args', {}).get('deployment', 'unknown')}"
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
            json={
                "model": _current_models[0],
                "max_tokens": 128,
                "messages": [
                    {"role": "system", "content": "You are an SRE assistant. Respond in exactly 2 sentences. First: what is directly observable in the output. Second: one next diagnostic action."},
                    {"role": "user",   "content": f"Command: {cmd}\n\nOutput:\n{stdout}"},
                ],
            },
            timeout=30,
        )
        return resp.json()["choices"][0]["message"].get("content", "").strip()
    except Exception:
        return stdout[:200]


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002)
