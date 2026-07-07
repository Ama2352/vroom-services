import os, json, uuid, threading, time
import redis as redis_lib
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

from memory import (search_memory as memory_search,
                    search_memory_items, format_incidents,
                    connect as redis_connect, build_symptom_text,
                    find_trusted_match, store_pending_suggestion, KNOWLEDGE_INDEX,
                    record_incident_occurrence, get_incident, list_incidents,
                    get_latest_incident, get_incident_timeline, append_incident_timeline, resolve_incident,
                    list_pending_suggestions, get_pending_suggestion,
                    approve_pending_suggestion, reject_pending_suggestion,
                    list_knowledge_entries, get_knowledge_entry, update_knowledge_entry,
                    delete_knowledge_entry, list_history_entries_for_knowledge,
                    get_history_entry, update_history_entry, delete_history_entry)
from collector import collect_bundle
from diagnostics import (collect_diagnostics, format_evidence,
                         collect_change_evidence, resolve_dependency)
from interpreter import interpret, _run_llm, DEFAULT_MODELS, GROQ_URL, OPENROUTER_URL
from seed import seed_if_empty

app = Flask(__name__)
CORS(app)  # the dashboard is a separate browser origin (its own NodePort)

REDIS_URL      = os.environ.get("REDIS_URL", "redis://redis.platform.svc.cluster.local:6379")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_KEY       = os.environ.get("GROQ_API_KEY", "")

rdb = redis_connect(REDIS_URL)

_MODELS_KEY    = "config:models"


def _load_models(rdb) -> list:
    raw = rdb.get(_MODELS_KEY)
    if raw:
        data = json.loads(raw)
        # Migrate: old string-format entries are invalid; clear and reinitialize.
        if data and isinstance(data[0], str):
            rdb.delete(_MODELS_KEY)
        else:
            return data
    rdb.set(_MODELS_KEY, json.dumps(DEFAULT_MODELS))
    return list(DEFAULT_MODELS)


_current_models: list = _load_models(rdb)


def _background_seed():
    try:
        n = seed_if_empty(rdb)
        print(f"[seed] seeded {n} knowledge/history entries", flush=True)
    except Exception as e:
        print(f"[seed] cold-start seed failed: {e}", flush=True)

threading.Thread(target=_background_seed, daemon=True).start()


def _format_trusted_match(match: dict) -> str:
    lines = [f"Known failure pattern: {match['root_cause_pattern']}",
             f"Fix: {match['fix_action']}"]
    if match.get("context_notes"):
        lines.append(f"Notes from a similar past occurrence: {match['context_notes']}")
    return "\n".join(lines)


_REFLECT_PROMPT = """\
You are analyzing a resolved incident to propose a knowledge-base update.
Existing knowledge keys: {existing_keys}

Incident:
Alert: {alert_name} on {service}
Root cause: {root_cause}
Fix command: {fix_command}

Output exactly this JSON (no markdown, no explanation):
{{"symptom":"one sentence describing this occurrence",
  "proposed_knowledge_key":"an existing key from the list above, or a new short_snake_case slug",
  "root_cause":"one sentence canonical root cause (used only if this is a new key)",
  "fix_action":"one sentence canonical fix (used only if this is a new key)",
  "context_notes":"anything specific to this occurrence (dates, values) or empty string"}}"""


def _reflect_and_store(rdb, incident: dict, fix_command: str) -> None:
    existing_keys = ", ".join(sorted(
        k.decode() if isinstance(k, bytes) else k for k in rdb.smembers(KNOWLEDGE_INDEX)
    )) or "(none yet)"

    _mock_mode = os.environ.get("LLM_MOCK", "").lower() == "true"
    if _mock_mode:
        proposed_key = "mock_key"
        suggestion = {
            "service":                incident["service"],
            "symptom":                f"Mock scenario: {os.environ.get('LLM_MOCK_SCENARIO', 'scale_to_zero')}",
            "proposed_knowledge_key": proposed_key,
            "is_new_knowledge_key":   not rdb.sismember(KNOWLEDGE_INDEX, proposed_key),
            "root_cause":             incident["root_cause"],
            "fix_action":             fix_command or "",
            "context_notes":          "",
            "source_incident_id":     incident.get("id", ""),
        }
        store_pending_suggestion(rdb, suggestion)
        print(f"[reflect] mock stored pending suggestion for {incident['service']}", flush=True)
        return

    if GROQ_KEY:
        url, key, model_id = GROQ_URL, GROQ_KEY, "llama-3.3-70b-versatile"
    elif OPENROUTER_KEY:
        first    = _current_models[0] if _current_models else {}
        model_id = first.get("id", "meta-llama/llama-3.3-70b-instruct:free") \
                   if isinstance(first, dict) else str(first)
        url, key = OPENROUTER_URL, OPENROUTER_KEY
    else:
        return

    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model_id, "max_tokens": 250, "temperature": 0.1,
                "messages": [{"role": "user", "content": _REFLECT_PROMPT.format(
                    existing_keys=existing_keys,
                    alert_name=incident["alert_name"], service=incident["service"],
                    root_cause=incident["root_cause"], fix_command=fix_command,
                )}],
            },
            timeout=30,
        )
        content = resp.json()["choices"][0]["message"].get("content", "").strip()
        content = content.replace("```json", "").replace("```", "").strip()
        parsed       = json.loads(content)
        proposed_key = parsed.get("proposed_knowledge_key", "").strip()
        suggestion = {
            "service":                incident["service"],
            "symptom":                parsed.get("symptom", ""),
            "proposed_knowledge_key": proposed_key,
            "is_new_knowledge_key":   not rdb.sismember(KNOWLEDGE_INDEX, proposed_key),
            "root_cause":             parsed.get("root_cause", ""),
            "fix_action":             parsed.get("fix_action", ""),
            "context_notes":          parsed.get("context_notes", ""),
            "source_incident_id":     incident.get("id", ""),
        }
        store_pending_suggestion(rdb, suggestion)
        print(f"[reflect] stored pending suggestion: {suggestion['symptom']}", flush=True)
    except Exception as e:
        print(f"[reflect] failed (non-fatal): {e}", flush=True)


_ADMIN_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Incident Agent Admin</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; font-family: monospace; }
body { background: #1a1a2e; color: #e0e0e0; padding: 20px; }
h1 { color: #4fc3f7; margin-bottom: 20px; font-size: 1.2em; }
.tabs { display: flex; gap: 2px; margin-bottom: 20px; }
.tab { padding: 8px 20px; background: #2a2a4a; border: none; color: #aaa; cursor: pointer; font-size: 0.9em; }
.tab.active { background: #0d47a1; color: #fff; }
.panel { display: none; }
.panel.active { display: block; }
textarea { width: 100%; background: #0d1117; color: #c9d1d9; border: 1px solid #30363d; padding: 10px; font-size: 0.85em; resize: vertical; line-height: 1.5; }
button { padding: 8px 16px; border: none; cursor: pointer; font-size: 0.85em; margin: 4px 2px; }
.btn-primary { background: #0d47a1; color: white; }
.btn-secondary { background: #37474f; color: #ccc; }
.btn-success { background: #1b5e20; color: white; }
.divider { border-top: 1px solid #30363d; margin: 20px 0; padding-top: 20px; }
label { display: block; color: #8b949e; font-size: 0.8em; margin-bottom: 4px; }
.row { display: flex; align-items: center; gap: 8px; margin: 8px 0; }
pre { background: #0d1117; border: 1px solid #30363d; padding: 12px; overflow-y: auto;
      white-space: pre-wrap; font-size: 0.8em; line-height: 1.5; max-height: 70vh; }
.toast { position: fixed; bottom: 20px; right: 20px; padding: 10px 18px; border-radius: 4px;
         font-size: 0.85em; opacity: 0; transition: opacity 0.3s; pointer-events: none; }
.toast.show { opacity: 1; }
.toast.ok  { background: #1b5e20; color: white; }
.toast.err { background: #b71c1c; color: white; }
.err-msg { color: #ef5350; font-size: 0.8em; margin-top: 4px; min-height: 1.2em; }
.kt-entry { border: 1px solid #30363d; padding: 8px; margin-bottom: 8px; }
input { background: #0d1117; color: #c9d1d9; border: 1px solid #30363d; padding: 6px;
        font-size: 0.85em; font-family: monospace; }
</style>
</head>
<body>
<h1>Incident Agent &#8212; Admin</h1>
<div class="tabs">
  <button class="tab active" onclick="switchTab('models')">Models</button>
</div>

<div id="tab-models" class="panel active">
  <div class="row">
    <label style="flex:1;margin:0;">Model Priority List (JSON array)</label>
    <button class="btn-primary" onclick="saveModels()">Save</button>
  </div>
  <textarea id="models-text" rows="10" spellcheck="false"></textarea>
  <div class="err-msg" id="models-error"></div>
</div>

<div class="toast" id="toast"></div>
<script>
const TABS = ["models"];
function switchTab(name) {
  TABS.forEach(t => {
    document.getElementById("tab-"+t).classList.toggle("active", t===name);
  });
  document.querySelectorAll(".tab").forEach((el,i) => {
    el.classList.toggle("active", TABS[i]===name);
  });
  if (name==="models") loadModels();
}
function showToast(msg, ok) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast show " + (ok ? "ok" : "err");
  setTimeout(() => { t.className = "toast"; }, 3000);
}
async function loadModels() {
  try {
    const r = await fetch("/admin/models");
    const d = await r.json();
    document.getElementById("models-text").value = JSON.stringify(d.models, null, 2);
  } catch(e) { showToast("Failed to load models", false); }
}
async function saveModels() {
  document.getElementById("models-error").textContent = "";
  let parsed;
  try   { parsed = JSON.parse(document.getElementById("models-text").value); }
  catch (e) { document.getElementById("models-error").textContent = "Invalid JSON: "+e.message; return; }
  try {
    const r = await fetch("/admin/models", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(parsed),
    });
    const d = await r.json();
    if (d.error) { document.getElementById("models-error").textContent = d.error; }
    else {
      showToast("Models saved", true);
      document.getElementById("models-text").value = JSON.stringify(d.models, null, 2);
    }
  } catch(e) { showToast("Save failed: "+e.message, false); }
}
loadModels();
</script>
</body>
</html>"""

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "incidents_in_memory": rdb.scard("incidents:index")})


@app.route("/memory/search")
def memory_search_endpoint():
    query = request.args.get("q", "").strip()
    limit = int(request.args.get("limit", "3"))
    if not query:
        return jsonify({"result": "no relevant memory found"})
    return jsonify({"result": memory_search(rdb, query, limit=limit)})


@app.route("/admin/ui")
def admin_ui():
    return _ADMIN_UI_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/admin/reset-incidents", methods=["POST"])
def admin_reset_incidents():
    keys = rdb.smembers("incidents:index")
    for key in keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        rdb.delete(f"incident:{key_str}")
    rdb.delete("incidents:index")
    return jsonify({"cleared": len(keys)})


@app.route("/admin/models", methods=["GET"])
def get_models():
    return jsonify({"models": _current_models})


@app.route("/admin/models", methods=["POST"])
def set_models():
    global _current_models
    data = request.get_json(silent=True)
    if not isinstance(data, list) or not data:
        return jsonify({"error": "body must be a non-empty JSON array"}), 400
    for m in data:
        if not isinstance(m, dict) or "id" not in m or "provider" not in m:
            return jsonify({"error": 'each model must be {"id": "...", "provider": "groq"|"openrouter"}'}), 400
    _current_models[:] = data
    rdb.set(_MODELS_KEY, json.dumps(data))
    return jsonify({"models": _current_models})


@app.route("/investigate", methods=["POST"])
def investigate():
    data       = request.get_json(silent=True) or {}
    alert_name = data.get("alert_name", "UnknownAlert")
    service    = data.get("service", "unknown")
    namespace  = data.get("namespace", "vroom-dev")
    pod        = data.get("pod", "")
    debug      = request.args.get("debug", "").lower() == "true"

    seed_if_empty(rdb)

    steps = []

    def _step(name: str, started_at: float, finished_at: float, **metadata) -> None:
        steps.append({
            "type": "step", "name": name,
            "started_at": started_at, "finished_at": finished_at,
            "duration_ms": int((finished_at - started_at) * 1000),
            "metadata": metadata,
        })

    t0     = time.time()
    bundle = collect_bundle(service, namespace)
    facts  = collect_diagnostics(service, namespace)
    t1     = time.time()
    _step("collect_diagnostics", t0, t1,
          pods_available=facts["pods_available"], pods_desired=facts["pods_desired"],
          waiting_reason=facts["waiting_reason"])

    t1a           = time.time()
    template_diff = collect_change_evidence(service, namespace)
    t1b           = time.time()
    _step("replicaset_diff", t1a, t1b, found=template_diff is not None)

    t1c        = time.time()
    dependency = resolve_dependency(facts["log_error"], facts["event_message"])
    t1d        = time.time()
    _step("dependency_chase", t1c, t1d, found=dependency is not None)

    facts = {**facts, "template_diff": template_diff, "dependency": dependency}

    print(f"[diag] {service}/{namespace}: pods={facts['pods_available']}/{facts['pods_desired']} "
          f"reason={facts['waiting_reason']!r} last_exit={facts['last_terminated_reason']!r} "
          f"restarts={facts['restarts']} "
          f"init={facts['init_waiting_reason']!r} init_last_exit={facts['init_last_terminated_reason']!r} "
          f"init_restarts={facts['init_restarts']} "
          f"log={'yes' if facts['log_error'] else 'none'} event={facts['event_reason']!r}", flush=True)

    query = build_symptom_text(alert_name, facts["waiting_reason"], facts["log_error"])

    t2            = time.time()
    match         = find_trusted_match(rdb, facts, query)
    trusted_match = match is not None
    memory_ctx    = _format_trusted_match(match) if match else ""
    t3            = time.time()
    _step("trusted_match_check", t2, t3, trusted_match=trusted_match)

    related_incidents_unconfirmed = []
    if not trusted_match:
        related_incidents_unconfirmed = search_memory_items(rdb, query, limit=3)

    print(f"[memory] trusted_match={trusted_match} "
          f"related_incidents={len(related_incidents_unconfirmed)}", flush=True)

    diagnosis = interpret(
        alert_name, service, namespace,
        facts, bundle, memory_ctx,
        models=_current_models,
        groq_key=GROQ_KEY,
        openrouter_key=OPENROUTER_KEY,
        pod=pod,
    )
    steps.extend(diagnosis.pop("_step_log", []))

    evidence = format_evidence(facts)

    occurrence = {
        "alert_name": alert_name, "service": service, "namespace": namespace,
        **facts,
        "root_cause":     diagnosis["root_cause"],
        "dev_action":     diagnosis["dev_action"],
        "kubectl_hint":   diagnosis["kubectl_hint"],
        "low_confidence": diagnosis.get("low_confidence", False),
    }
    t6          = time.time()
    incident_id = record_incident_occurrence(rdb, occurrence)
    t7          = time.time()
    _step("record_incident", t6, t7, incident_id=incident_id)

    for s in steps:
        append_incident_timeline(rdb, incident_id, s)

    threading.Thread(
        target=_reflect_and_store,
        args=(rdb, {
            "alert_name": alert_name,
            "service":    service,
            "root_cause": diagnosis["root_cause"],
            "id":         incident_id,
        }, diagnosis["kubectl_hint"]),
        daemon=True,
    ).start()

    return jsonify({
        "service":          service,
        "alert_name":       alert_name,
        "namespace":        namespace,
        "incident_id":      incident_id,
        "root_cause":       diagnosis["root_cause"],
        "dev_action":       diagnosis["dev_action"],
        "kubectl_hint":     diagnosis["kubectl_hint"],
        "evidence_snippet": evidence,
        "trusted_match":    trusted_match,
        **({"related_incidents_unconfirmed": related_incidents_unconfirmed} if not trusted_match else {}),
        "low_confidence":   diagnosis.get("low_confidence", False),
        **({"debug": {
            "bundle":         bundle,
            "memory_context": memory_ctx or "(none)",
            "facts":          facts,
        }} if debug else {}),
    })


def _incident_detail_payload(iid: str, incident: dict) -> dict:
    timeline = get_incident_timeline(rdb, iid)
    matches  = [p for p in list_pending_suggestions(rdb) if p.get("source_incident_id") == iid]
    return {**incident, "timeline": timeline,
            "pending_suggestion": matches[0] if matches else None}


@app.route("/incidents", methods=["GET"])
def list_incidents_route():
    status    = request.args.get("status")
    incidents = list_incidents(rdb, status=status)
    incidents.sort(key=lambda i: int(i.get("timestamp") or 0), reverse=True)
    return jsonify({"incidents": [
        {"id": i["id"], "alert_name": i["alert_name"], "service": i["service"],
         "status": i["status"], "timestamp": int(i.get("timestamp") or 0),
         "root_cause": i.get("root_cause", "")}
        for i in incidents
    ]})


@app.route("/incidents/latest", methods=["GET"])
def latest_incident_route():
    incident = get_latest_incident(rdb)
    if incident is None:
        return jsonify({"incident": None})
    return jsonify({"incident": _incident_detail_payload(incident["id"], incident)})


@app.route("/incidents/<iid>", methods=["GET"])
def incident_detail_route(iid):
    incident = get_incident(rdb, iid)
    if incident is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"incident": _incident_detail_payload(iid, incident)})


@app.route("/incidents/<iid>/resolve", methods=["POST"])
def resolve_incident_route(iid):
    data  = request.get_json(silent=True) or {}
    actor = (data.get("actor") or "").strip()
    if not actor:
        return jsonify({"error": "actor is required"}), 400
    if not resolve_incident(rdb, iid, actor):
        return jsonify({"error": "not found"}), 404
    return jsonify({"resolved": True})


@app.route("/pending", methods=["GET"])
def list_pending_route():
    status = request.args.get("status", "pending")
    return jsonify({"pending": list_pending_suggestions(rdb, status=status)})


@app.route("/pending/<pid>", methods=["GET"])
def pending_detail_route(pid):
    item = get_pending_suggestion(rdb, pid)
    if item is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"pending": item})


@app.route("/pending/<pid>/approve", methods=["POST"])
def approve_pending_route(pid):
    data          = request.get_json(silent=True) or {}
    actor         = (data.get("actor") or "").strip()
    mode          = data.get("mode")
    knowledge_key = (data.get("knowledge_key") or "").strip()
    if not actor or mode not in ("existing", "new") or not knowledge_key:
        return jsonify({"error": "actor, mode ('existing'|'new'), and knowledge_key are required"}), 400
    hid = approve_pending_suggestion(
        rdb, pid, actor, mode, knowledge_key,
        data.get("symptom", ""), data.get("context_notes", ""),
        root_cause_pattern=data.get("root_cause_pattern"),
        fix_action=data.get("fix_action"),
        conclusive=bool(data.get("conclusive", False)),
    )
    if hid is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"approved": True, "history_id": hid})


@app.route("/pending/<pid>/reject", methods=["POST"])
def reject_pending_route(pid):
    data  = request.get_json(silent=True) or {}
    actor = (data.get("actor") or "").strip()
    if not actor:
        return jsonify({"error": "actor is required"}), 400
    if not reject_pending_suggestion(rdb, pid, actor, data.get("decision_reason")):
        return jsonify({"error": "not found"}), 404
    return jsonify({"rejected": True})


@app.route("/knowledge", methods=["GET"])
def list_knowledge_route():
    out = []
    for e in list_knowledge_entries(rdb):
        out.append({**e, "history_count": len(list_history_entries_for_knowledge(rdb, e["key"]))})
    return jsonify({"knowledge": out})


@app.route("/knowledge/<key>", methods=["GET"])
def knowledge_detail_route(key):
    entry = get_knowledge_entry(rdb, key)
    if entry is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"knowledge": entry, "history": list_history_entries_for_knowledge(rdb, key)})


@app.route("/knowledge/<key>", methods=["PUT"])
def update_knowledge_route(key):
    data  = request.get_json(silent=True) or {}
    actor = (data.get("actor") or "").strip()
    if not actor:
        return jsonify({"error": "actor is required"}), 400
    ok = update_knowledge_entry(rdb, key, {
        "root_cause_pattern": data.get("root_cause_pattern", ""),
        "fix_action":         data.get("fix_action", ""),
        "conclusive":         bool(data.get("conclusive", False)),
        "last_modified_by":   actor,
    })
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"updated": True})


@app.route("/knowledge/<key>", methods=["DELETE"])
def delete_knowledge_route(key):
    result = delete_knowledge_entry(rdb, key)
    if result == "not_found":
        return jsonify({"error": "not found"}), 404
    if result == "has_history":
        return jsonify({"error": "cannot delete: history entries reference this key"}), 409
    return jsonify({"deleted": True})


@app.route("/history/<hid>", methods=["PUT"])
def update_history_route(hid):
    data  = request.get_json(silent=True) or {}
    actor = (data.get("actor") or "").strip()
    if not actor:
        return jsonify({"error": "actor is required"}), 400
    ok = update_history_entry(rdb, hid, {
        "symptom":          data.get("symptom", ""),
        "context_notes":    data.get("context_notes", ""),
        "last_modified_by": actor,
    })
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"updated": True})


@app.route("/history/<hid>", methods=["DELETE"])
def delete_history_route(hid):
    if not delete_history_entry(rdb, hid):
        return jsonify({"error": "not found"}), 404
    return jsonify({"deleted": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002)
