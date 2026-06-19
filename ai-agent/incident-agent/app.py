import os, json, uuid
import redis as redis_lib
import requests
from flask import Flask, request, jsonify

from memory import store_incident, search_memory as memory_search, connect as redis_connect
from collector import collect_bundle
from react_loop import run_react_loop
from tools import call_tool
from seed import seed_if_empty

app = Flask(__name__)

REDIS_URL       = os.environ.get("REDIS_URL", "redis://redis.platform.svc.cluster.local:6379")
OPENROUTER_KEY  = os.environ.get("OPENROUTER_API_KEY", "")
EXECUTOR_URL    = os.environ.get("KUBECTL_EXECUTOR_URL", "http://kubectl-executor.monitoring.svc.cluster.local:5001")
EXECUTOR_TOKEN  = os.environ.get("EXECUTOR_API_KEY", "change-me")
PENDING_TTL     = 3600  # seconds — matches n8n Wait node timeout

rdb = redis_connect(REDIS_URL)
try:
    seed_if_empty(rdb)
except Exception as _e:
    print(f"[warn] cold-start seed failed (Redis may not be ready): {_e}")


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

    diagnosis = run_react_loop(alert, _dispatch_tool, OPENROUTER_KEY)

    eid = str(uuid.uuid4())
    rdb.setex(f"pending:{eid}", PENDING_TTL, json.dumps({"alert": alert, "diagnosis": diagnosis}))

    return jsonify({
        "execution_id":        eid,
        "service":             service,
        "alert_name":          alert_name,
        "root_cause":          diagnosis["root_cause"],
        "confidence":          diagnosis["confidence"],
        "investigation_steps": len(diagnosis.get("investigation_steps", [])),
        "remediation":         diagnosis.get("remediation"),
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
    r = requests.post(f"{EXECUTOR_URL}/tools/restart", json=rem.get("args", {}), headers=headers, timeout=35)
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
    deployment = remediation.get("args", {}).get("deployment", "unknown")
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
            json={
                "model": "meta-llama/llama-3.1-8b-instruct:free",
                "max_tokens": 128,
                "messages": [
                    {"role": "system", "content": "You are an SRE assistant. Respond in exactly 2 sentences. First: what is directly observable in the output. Second: one next diagnostic action."},
                    {"role": "user",   "content": f"Command: kubectl rollout restart deployment/{deployment}\n\nOutput:\n{stdout}"},
                ],
            },
            timeout=30,
        )
        return resp.json()["choices"][0]["message"].get("content", "").strip()
    except Exception:
        return stdout[:200]


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002)
