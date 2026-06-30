import os, json, uuid, threading, time
import redis as redis_lib
import requests
from flask import Flask, request, jsonify

from memory import (store_incident, search_memory as memory_search,
                    connect as redis_connect,
                    store_runbook_entry, get_runbook_entries, search_runbook)
from collector import collect_bundle
from diagnostics import collect_diagnostics, format_evidence
from interpreter import (interpret, _run_llm, DEFAULT_MODELS,
                         GROQ_URL, OPENROUTER_URL, K8S_KNOWLEDGE_TABLE)
from seed import seed_if_empty

app = Flask(__name__)

REDIS_URL      = os.environ.get("REDIS_URL", "redis://redis.platform.svc.cluster.local:6379")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_KEY       = os.environ.get("GROQ_API_KEY", "")

rdb = redis_connect(REDIS_URL)

_MODELS_KEY    = "config:models"
_KNOWLEDGE_KEY = "config:knowledge_table"


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


def _load_knowledge_table(rdb) -> str:
    raw = rdb.get(_KNOWLEDGE_KEY)
    if raw:
        return raw.decode() if isinstance(raw, bytes) else raw
    rdb.set(_KNOWLEDGE_KEY, K8S_KNOWLEDGE_TABLE)
    return K8S_KNOWLEDGE_TABLE


_current_knowledge_table: str = _load_knowledge_table(rdb)


def _background_seed():
    try:
        n = seed_if_empty(rdb)
        print(f"[seed] seeded {n} runbook entries", flush=True)
    except Exception as e:
        print(f"[seed] cold-start seed failed: {e}", flush=True)

threading.Thread(target=_background_seed, daemon=True).start()


def _format_memory_context(mem_text: str, runbook_hits: list) -> str:
    parts = []
    if mem_text and mem_text != "no relevant memory found":
        parts.append(f"Past incidents:\n{mem_text}")
    if runbook_hits:
        lines = [
            f"- {h['title']} ({h['service']}): {h['symptom']} → Fix: {h['fix_command']}"
            for h in runbook_hits
        ]
        parts.append("Runbook:\n" + "\n".join(lines))
    return "\n\n".join(parts) if parts else ""



def _reflect_and_store(rdb, incident: dict, fix_command: str) -> None:
    _mock_mode = os.environ.get("LLM_MOCK", "").lower() == "true"
    if _mock_mode:
        scenario = os.environ.get("LLM_MOCK_SCENARIO", "scale_to_zero")
        entry = {
            "title":       f"Mock: {incident['alert_name']} on {incident['service']}",
            "service":     incident["service"],
            "symptom":     f"Mock scenario: {scenario}",
            "root_cause":  incident["root_cause"],
            "fix_command": fix_command or "",
            "source":      "learned",
        }
        store_runbook_entry(rdb, entry)
        print(f"[reflect] mock stored: {entry['title']}", flush=True)
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
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={
                "model":       model_id,
                "max_tokens":  200,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": (
                        "You are a technical writer for an SRE runbook. "
                        "Based on this resolved incident, write ONE concise runbook entry. "
                        "Output ONLY a valid JSON object, no markdown: "
                        '{"title":"...","service":"...","symptom":"one sentence",'
                        '"root_cause":"one sentence","fix_command":"exact kubectl command"}'
                    )},
                    {"role": "user", "content": (
                        f"Alert: {incident['alert_name']} on {incident['service']}\n"
                        f"Root cause: {incident['root_cause']}\n"
                        f"Command: {fix_command}\nOutcome: resolved"
                    )},
                ],
            },
            timeout=30,
        )
        content = resp.json()["choices"][0]["message"].get("content", "").strip()
        content = content.replace("```json", "").replace("```", "").strip()
        entry = json.loads(content)
        entry["source"] = "learned"
        store_runbook_entry(rdb, entry)
        print(f"[reflect] stored: {entry.get('title', '')}", flush=True)
    except Exception as e:
        print(f"[reflect] failed (non-fatal): {e}", flush=True)


_SUGGEST_PROMPT = """\
You are updating a Kubernetes diagnostic knowledge table used by an incident response agent.
Each entry follows this exact format:
- <WaitingReason or pattern>: One-line description of what this means.
  This IS / is NOT a conclusive root cause.
  Look for: specific things to investigate.
  Primary source: <exact kubectl command>.

Here is raw incident data (kubectl output, logs, agent answer, or your notes):
{raw}

Write exactly ONE new entry in the format above.
Output only the bullet text — no explanation, no markdown, no preamble."""

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


@app.route("/admin/runbook")
def admin_runbook():
    entries   = get_runbook_entries(rdb)
    bootstrap = [e for e in entries if e.get("source") == "bootstrap"]
    learned   = [e for e in entries if e.get("source") == "learned"]

    if not entries:
        return app.response_class(
            "# Vroom Operations Runbook\n\n*No entries. POST /admin/reseed to bootstrap.*\n",
            mimetype="text/plain",
        )

    lines = [
        "# Vroom Operations Runbook\n",
        f"*{len(entries)} entries — {len(bootstrap)} bootstrap, {len(learned)} learned.*\n",
    ]
    for e in entries:
        ts       = int(e.get("timestamp", 0))
        date_str = time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else "unknown"
        lines.append(f"\n## {e.get('title', 'Untitled')}")
        lines.append(f"**Service:** {e.get('service', '')}")
        lines.append(f"**Symptom:** {e.get('symptom', '')}")
        lines.append(f"**Root cause:** {e.get('root_cause', '')}")
        if e.get("fix_command"):
            lines.append(f"**Fix:** `{e['fix_command']}`")
        lines.append(f"*Source: {e.get('source', 'unknown')} | {date_str}*")
    return app.response_class("\n".join(lines), mimetype="text/plain")


@app.route("/admin/reset-incidents", methods=["POST"])
def admin_reset_incidents():
    keys = rdb.smembers("incidents:index")
    for key in keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        rdb.delete(f"incident:{key_str}")
    rdb.delete("incidents:index")
    return jsonify({"cleared": len(keys)})


@app.route("/admin/reseed", methods=["POST"])
def admin_reseed():
    keys = rdb.smembers("runbook:index")
    for key in keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        rdb.delete(f"runbook:entry:{key_str}")
    rdb.delete("runbook:index")
    n = seed_if_empty(rdb)
    return jsonify({"seeded": n})


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


@app.route("/admin/knowledge", methods=["GET"])
def get_knowledge():
    return jsonify({"table": _current_knowledge_table})


@app.route("/admin/knowledge", methods=["POST"])
def set_knowledge():
    global _current_knowledge_table
    data = request.get_json(silent=True) or {}
    text = data.get("table", "")
    if not isinstance(text, str):
        return jsonify({"error": "'table' must be a string"}), 400
    _current_knowledge_table = text
    rdb.set(_KNOWLEDGE_KEY, text)
    return jsonify({"saved": True, "length": len(text)})


@app.route("/admin/knowledge/suggest", methods=["POST"])
def suggest_knowledge_entry():
    data = request.get_json(silent=True) or {}
    raw  = data.get("raw", "").strip()
    if not raw:
        return jsonify({"error": "body must include non-empty 'raw' field"}), 400
    messages   = [{"role": "user",
                   "content": _SUGGEST_PROMPT.format(raw=raw)}]
    suggestion = _run_llm(messages, None, _current_models, GROQ_KEY, OPENROUTER_KEY)
    if not suggestion:
        return jsonify({"suggestion": "",
                        "error": "LLM returned empty response — check API keys and model list"})
    return jsonify({"suggestion": suggestion.strip()})


@app.route("/investigate", methods=["POST"])
def investigate():
    data       = request.get_json(silent=True) or {}
    alert_name = data.get("alert_name", "UnknownAlert")
    service    = data.get("service", "unknown")
    namespace  = data.get("namespace", "vroom-dev")
    pod        = data.get("pod", "")
    debug      = request.args.get("debug", "").lower() == "true"

    seed_if_empty(rdb)

    query        = f"{alert_name} {service}"
    mem_text     = memory_search(rdb, query, limit=3)
    runbook_hits = search_runbook(rdb, query, top_k=3)
    memory_ctx   = _format_memory_context(mem_text, runbook_hits)

    incident_hits = 0 if (not mem_text or mem_text == "no relevant memory found") \
                    else len([l for l in mem_text.splitlines() if l.strip()])
    print(f"[memory] pre-fetch: incidents={incident_hits} runbook={len(runbook_hits)} "
          f"ctx_len={len(memory_ctx)}", flush=True)

    bundle = collect_bundle(service, namespace)
    facts  = collect_diagnostics(service, namespace)
    print(f"[diag] {service}/{namespace}: pods={facts['pods_available']}/{facts['pods_desired']} "
          f"reason={facts['waiting_reason']!r} last_exit={facts['last_terminated_reason']!r} "
          f"restarts={facts['restarts']} "
          f"init={facts['init_waiting_reason']!r} init_last_exit={facts['init_last_terminated_reason']!r} "
          f"init_restarts={facts['init_restarts']} "
          f"log={'yes' if facts['log_error'] else 'none'} event={facts['event_reason']!r}", flush=True)

    diagnosis = interpret(
        alert_name, service, namespace,
        facts, bundle, memory_ctx,
        models=_current_models,
        groq_key=GROQ_KEY,
        openrouter_key=OPENROUTER_KEY,
        pod=pod,
        knowledge_table=_current_knowledge_table,
    )

    evidence = format_evidence(facts)

    store_incident(rdb, {
        "alert_name":     alert_name,
        "service":        service,
        "namespace":      namespace,
        "symptoms":       bundle,
        "waiting_reason": facts["waiting_reason"],
        "log_error":      facts["log_error"],
        "root_cause":     diagnosis["root_cause"],
        "kubectl_hint":   diagnosis["kubectl_hint"],
        "outcome":        "acknowledged",
    })

    threading.Thread(
        target=_reflect_and_store,
        args=(rdb, {
            "alert_name": alert_name,
            "service":    service,
            "root_cause": diagnosis["root_cause"],
        }, diagnosis["kubectl_hint"]),
        daemon=True,
    ).start()

    return jsonify({
        "service":          service,
        "alert_name":       alert_name,
        "namespace":        namespace,
        "root_cause":       diagnosis["root_cause"],
        "dev_action":       diagnosis["dev_action"],
        "kubectl_hint":     diagnosis["kubectl_hint"],
        "evidence_snippet": evidence,
        "memory_hits":      {"incidents": incident_hits, "runbook": len(runbook_hits)},
        "low_confidence":   diagnosis.get("low_confidence", False),
        **({"debug": {
            "bundle":         bundle,
            "memory_context": memory_ctx or "(none)",
            "facts":          facts,
        }} if debug else {}),
    })



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002)
