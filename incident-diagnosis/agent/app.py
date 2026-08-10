import os, json, uuid, threading, time, re
try:
    import resource
except ImportError:  # pragma: no cover - Windows local development
    resource = None
from datetime import datetime, timezone
from pathlib import Path
import redis as redis_lib
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

from memory import (search_memory as memory_search,
                    connect as redis_connect,
                    store_pending_suggestion, KNOWLEDGE_INDEX,
                    record_incident_occurrence, get_incident, list_incidents,
                    get_latest_incident, get_incident_timeline, append_incident_timeline, resolve_incident,
                    get_incident_occurrences,
                    list_pending_suggestions, get_pending_suggestion,
                    approve_pending_suggestion, reject_pending_suggestion,
                    list_knowledge_entries, get_knowledge_entry, update_knowledge_entry,
                    delete_knowledge_entry, list_history_entries_for_knowledge,
                    get_history_entry, update_history_entry, delete_history_entry,
                    store_knowledge_entry, list_all_history_entries)
from collector import collect_bundle, collect_impact
from alerting import normalize_alert, incident_window
from correlation import collect_log_evidence, correlate_trace, derive_log_error
from routing import route_incident
from confidence import align_root_cause_confidence, assess_confidence
from diagnostics import (collect_diagnostics,
                          collect_change_evidence, resolve_dependency,
                          collect_workload_deployment,
                          collect_gitops_deployed_change)
from github_client import repository_clients_from_env
from provenance import (classify_causality, collect_deployed_identity,
                        collect_service_source_evidence, combine_provenance,
                        summarize_provenance)
from finalization import finalize_diagnosis
from presentation import build_presentation
from interpreter import interpret, _run_llm, DEFAULT_MODELS, GROQ_URL, OPENROUTER_URL
from seed import seed_if_empty
from retrieval.models import RetrievalMode
from retrieval.service import create_retrieval_service

app = Flask(__name__)
CORS(app)  # the dashboard is a separate browser origin (its own NodePort)

REDIS_URL      = os.environ.get("REDIS_URL", "redis://redis.platform.svc.cluster.local:6379")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_KEY       = os.environ.get("GROQ_API_KEY", "")

rdb = redis_connect(REDIS_URL)
retrieval_service = create_retrieval_service(
    rdb,
    Path(os.environ.get("RERANKER_MODEL_DIR", "/opt/models/minilm")),
)
_repository_clients = repository_clients_from_env()


def _rss_mib() -> int:
    """Return process high-water RSS in MiB without exposing host details."""
    if resource is None:
        return 0
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # Linux reports KiB; macOS reports bytes. The service runs on Linux, but
    # keeping this portable makes the audit helper safe in local tests.
    return max(0, value // 1024 if value > 10_000 else value // (1024 * 1024))


def _invalidate_retrieval_snapshot() -> None:
    retrieval_service.corpus.invalidate()

_MODELS_KEY    = "config:models"
_KNOWLEDGE_KEY_RE = re.compile(r'^[a-z][a-z0-9_]*$')


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

    incident_full = get_incident(rdb, incident.get("id")) if incident.get("id") else None
    from retrieval.signals import select_unique_signal
    trigger_waiting_reason = select_unique_signal(incident_full) if incident_full else ""

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
            "trigger_waiting_reason": trigger_waiting_reason,
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
            "trigger_waiting_reason": trigger_waiting_reason,
        }
        store_pending_suggestion(rdb, suggestion)
        print(f"[reflect] stored pending suggestion: {suggestion['symptom']}", flush=True)
    except Exception as e:
        print(f"[reflect] failed (non-fatal): {e}", flush=True)


def _should_reflect(diagnosis: dict, diagnosis_confidence: dict) -> bool:
    decision = diagnosis.get("diagnosis_decision") or {}
    return (
        decision.get("published_generated_answer") is True
        and decision.get("status") in {"accepted", "accepted_after_refine"}
        and diagnosis_confidence.get("level") in {"high", "medium"}
    )


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


def _collect_dual_provenance(
    service: str,
    namespace: str,
    template_diff: dict | None,
    log_evidence: dict,
    trace_handoff: dict,
    alert_started_at: str,
) -> dict:
    """Correlate the current workload's GitOps and source revisions before classification."""
    deployed = collect_deployed_identity(service, namespace, collect_workload_deployment)
    image = deployed.get("image", "")
    source = (
        collect_service_source_evidence(image, _repository_clients.services, service=service)
        if deployed.get("status") == "found"
        else {"status": "unavailable", "reason": deployed.get("reason", "workload_image_unavailable")}
    )
    gitops = collect_gitops_deployed_change(
        service, namespace, template_diff, _repository_clients.gitops,
    )
    dual = combine_provenance(gitops, source)
    causal = classify_causality(
        dual, service, log_evidence, trace_handoff, alert_started_at, failure_predates=False,
        template_diff=template_diff,
    )
    return summarize_provenance({
        **({key: value for key, value in gitops.items() if key != "status"} if gitops.get("status") == "found" else {}),
        "classification": gitops.get("classification", "unavailable"),
        "service": service,
        "dual": dual,
        "causal_status": {
            "status": causal.status,
            "reason_codes": list(causal.reason_codes),
            "matched_identifiers": list(causal.matched_identifiers),
        },
    })


@app.route("/investigate", methods=["POST"])
def investigate():
    data       = request.get_json(silent=True) or {}
    normalized = normalize_alert(data)
    alert_name = normalized["alert_name"]
    service    = normalized["service"]
    namespace  = normalized["namespace"]
    pod        = normalized["pod"]
    debug      = request.args.get("debug", "").lower() == "true"

    seed_if_empty(rdb)

    steps = []

    impact = {"status": "unavailable", "window": "5m", "request_rate": None,
              "error_rate_percent": None, "p99_seconds": None,
              "errors": ["alert has no starts_at; incident window unavailable"]}
    log_evidence = {"status": "unavailable", "errors": ["alert has no starts_at"]}
    trace_handoff = {"status": "unavailable"}
    if normalized.get("starts_at") and not normalized.get("starts_at_error"):
        try:
            start_s, end_s = incident_window(normalized["starts_at"], datetime.now(timezone.utc))
            impact = collect_impact(service, namespace, alert=normalized)
            log_evidence = collect_log_evidence(service, namespace, start_s, end_s)
            trace_handoff = correlate_trace(log_evidence, start_s, end_s)
        except (TypeError, ValueError) as exc:
            impact["errors"] = [str(exc)]
    diagnosis_confidence = assess_confidence(normalized, impact, log_evidence, trace_handoff, {
        "kubernetes": facts.get("waiting_reason", "") if "facts" in locals() else "",
    })

    def _step(name: str, started_at: float, finished_at: float, **metadata) -> None:
        if debug and name in {"trusted_match_check", "llm_phase1", "llm_refine", "hard_validation",
                              "semantic_critic", "hard_validation_refine", "semantic_critic_refine"}:
            metadata["rss_mib"] = _rss_mib()
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

    change_services = []
    if normalized.get("incident_kind") == "dlq" and trace_handoff.get("status") == "correlated":
        change_services.extend(
            traced_service for traced_service in trace_handoff.get("involved_services", [])
            if traced_service and traced_service != service
        )
    change_services.append(service)

    t1a = time.time()
    selected_change: tuple[str, dict, dict] | None = None
    provenance_services: list[dict] = []
    best_rank = -1
    involved_services = list(trace_handoff.get("involved_services", []))
    for candidate_service in dict.fromkeys(change_services):
        candidate_diff = collect_change_evidence(candidate_service, namespace)
        if candidate_diff is None:
            continue
        candidate_diff = {**candidate_diff, "service": candidate_service}
        candidate_provenance = _collect_dual_provenance(
            candidate_service, namespace, candidate_diff, log_evidence,
            trace_handoff, normalized.get("starts_at", ""),
        )
        status = candidate_provenance.get("causal_status", {}).get("status", "unavailable")
        role = "alert_target" if candidate_service == service else (
            "producer" if candidate_service in involved_services[:involved_services.index(service)] else "downstream"
        ) if service in involved_services else "related"
        dual = candidate_provenance.get("dual") or {}
        source = dual.get("service_source") or {}
        provenance_services.append({
            "service": candidate_service, "role": role,
            "source": "Manual runtime change" if candidate_diff.get("env_changed") and candidate_provenance.get("classification") == "hotfix" else "Service source revision",
            "relationship": "Confirmed cause" if status == "causal_candidate" else "Related context" if status == "recent_context" else "Unavailable",
            "source_revision": (source.get("commit") or {}).get("sha") if isinstance(source.get("commit"), dict) else None,
            "source_relevance": source.get("source_relevance"),
        })
        rank = {"causal_candidate": 3, "recent_context": 2, "unavailable": 1, "conflicting": 0}.get(status, 0)
        if selected_change is None or rank > best_rank:
            selected_change = (candidate_service, candidate_diff, candidate_provenance)
            best_rank = rank
        if status == "causal_candidate":
            break

    if selected_change is None:
        change_service, template_diff, provenance = service, None, None
    else:
        change_service, template_diff, provenance = selected_change
    t1b           = time.time()
    _step("replicaset_diff", t1a, t1b, found=template_diff is not None, service=change_service)

    t1c        = time.time()
    dependency = resolve_dependency(facts["log_error"], facts["event_message"])
    t1d        = time.time()
    _step("dependency_chase", t1c, t1d, found=dependency is not None)

    t1e        = time.time()
    t1f        = time.time()
    _step("provenance_lookup", t1e, t1f,
          classification=(provenance or {}).get("classification"))

    if provenance is not None and template_diff is not None:
        affected_fields = [
            f"env.{change.get('key')}" for change in template_diff.get("env_diff", [])
            if change.get("key")
        ]
        if template_diff.get("image_changed"):
            affected_fields.append("image")
        provenance = {
            **provenance,
            "services": provenance_services[:3],
            "service": change_service,
            "alert_started_at": normalized.get("starts_at", ""),
            "affected_fields": affected_fields,
        }

    facts = {**facts, "template_diff": template_diff, "dependency": dependency, "provenance": provenance}
    if log_evidence.get("status") == "found":
        # The selected structured Loki record is canonical for diagnosis; the
        # legacy latest-error query remains only a collector fallback.
        facts["log_error"] = derive_log_error(log_evidence)
    elif log_evidence.get("status") == "no_match":
        facts["log_error"] = ""

    diagnosis_confidence = assess_confidence(normalized, impact, log_evidence, trace_handoff, {
        "kubernetes": facts.get("waiting_reason"),
        "changes": template_diff,
        "dependencies": dependency,
    })

    evidence_bundle = {
        "impact": {"triggering_metric": {
            "name": "dlq_events" if normalized.get("incident_kind") == "dlq" else "service_impact",
            "value": normalized.get("metric_value"),
            "threshold": normalized.get("threshold"),
        }} if impact.get("status") == "available" else {},
        "log_evidence": log_evidence,
        "trace_handoff": trace_handoff,
        "template_diff": template_diff,
        "provenance": provenance,
        "k8s_state": facts,
        "k8s_event": {"id": facts.get("event_object"), "reason": facts.get("event_reason"), "message": facts.get("event_message")},
        "dependency": dependency,
        "dlq_state": {"value": normalized.get("metric_value")} if normalized.get("incident_kind") == "dlq" else {},
    }
    routing = route_incident(normalized, evidence_bundle)
    evidence_chain = routing.evidence_chain
    _step("routing", time.time(), time.time(), **routing.to_api_dict())
    _step("evidence_chain", time.time(), time.time(), incident_kind=evidence_chain["incident_kind"],
          primary=[item["id"] for item in evidence_chain["primary"]],
          secondary=[item["id"] for item in evidence_chain["secondary"]])

    print(f"[diag] {service}/{namespace}: pods={facts['pods_available']}/{facts['pods_desired']} "
          f"reason={facts['waiting_reason']!r} last_exit={facts['last_terminated_reason']!r} "
          f"restarts={facts['restarts']} "
          f"init={facts['init_waiting_reason']!r} init_last_exit={facts['init_last_terminated_reason']!r} "
          f"init_restarts={facts['init_restarts']} "
          f"log={'yes' if facts['log_error'] else 'none'} event={facts['event_reason']!r}", flush=True)

    t2            = time.time()
    retrieval     = retrieval_service.retrieve(alert_name, facts, routing)
    trusted_match = retrieval.mode is RetrievalMode.EXACT_CONCLUSIVE
    t3            = time.time()
    _step(
        "trusted_match_check", t2, t3,
        trusted_match=trusted_match,
        retrieval_mode=retrieval.mode.value,
        retrieval_accepted=retrieval.accepted,
    )

    print(f"[memory] trusted_match={trusted_match} "
          f"retrieval_mode={retrieval.mode.value} "
          f"accepted={retrieval.accepted}", flush=True)

    try:
        diagnosis = dict(interpret(
            alert_name, service, namespace,
            facts, bundle, retrieval,
            models=_current_models,
            groq_key=GROQ_KEY,
            openrouter_key=OPENROUTER_KEY,
            pod=pod,
            chain=evidence_chain,
        ))
    except MemoryError:
        print(f"[diag] investigation capacity exhausted service={service} namespace={namespace}", flush=True)
        return jsonify({"error": "investigation capacity exhausted", "retryable": True}), 503
    diagnosis = finalize_diagnosis(diagnosis, evidence_chain, namespace, service)
    diagnosis["root_cause"] = align_root_cause_confidence(
        diagnosis["root_cause"], diagnosis_confidence,
    )
    diagnosis["low_confidence"] = bool(diagnosis.get("low_confidence")) or diagnosis_confidence["level"] in {"low", "unknown"}
    steps.extend(diagnosis.pop("_step_log", []))

    presentation = build_presentation(
        alert=normalized,
        diagnosis=diagnosis,
        diagnosis_confidence=diagnosis_confidence,
        evidence_chain=evidence_chain,
        facts=facts,
        impact=impact,
        log_evidence=log_evidence,
        trace_handoff=trace_handoff,
        retrieval_support=retrieval.to_api_dict(debug=False),
    )

    occurrence = {
        "alert_name": alert_name, "service": service, "namespace": namespace,
        **facts,
        "root_cause":     diagnosis["root_cause"],
        "dev_action":     diagnosis["dev_action"],
        "kubectl_hint":   diagnosis["kubectl_hint"],
        "low_confidence": diagnosis.get("low_confidence", False),
        "impact": impact, "log_evidence": log_evidence,
        "trace_handoff": trace_handoff, "diagnosis_confidence": diagnosis_confidence,
        "evidence_chain": evidence_chain,
        "diagnosis_decision": diagnosis.get("diagnosis_decision", {}),
        "causal_chain_summary": diagnosis.get("causal_chain_summary", {}),
        "presentation": presentation,
        "retrieval_support": retrieval.to_api_dict(debug=False),
    }
    t6          = time.time()
    incident_id = record_incident_occurrence(rdb, occurrence)
    t7          = time.time()
    _step("record_incident", t6, t7, incident_id=incident_id)

    for s in steps:
        append_incident_timeline(rdb, incident_id, s)

    if _should_reflect(diagnosis, diagnosis_confidence):
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
        "retrieval_support": retrieval.to_api_dict(debug=debug),
        "low_confidence":   diagnosis.get("low_confidence", False),
        "trace_handoff": trace_handoff,
        "diagnosis_confidence": diagnosis_confidence,
        "diagnosis_decision": diagnosis.get("diagnosis_decision", {}),
        **({"debug": {
            "bundle":         bundle,
            "retrieval_support": retrieval.to_api_dict(debug=True),
            "facts":          facts,
            "routing":        routing.to_api_dict(include_signals=True),
            "evidence_chain": evidence_chain,
        }} if debug else {}),
    })


def _incident_detail_payload(iid: str, incident: dict) -> dict:
    timeline = get_incident_timeline(rdb, iid)
    occurrences = get_incident_occurrences(rdb, iid)
    published = (incident.get("diagnosis_decision") or {}).get("published_generated_answer", True)
    matches = [
        p for p in list_pending_suggestions(rdb)
        if published and p.get("source_incident_id") == iid
    ]
    return {**incident, "timeline": timeline, "occurrences": occurrences,
            "selected_occurrence": max(len(occurrences) - 1, 0),
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
        trigger_waiting_reason=data.get("trigger_waiting_reason", ""),
    )
    if hid is None:
        return jsonify({"error": "not found"}), 404
    _invalidate_retrieval_snapshot()
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


@app.route("/knowledge", methods=["POST"])
def create_knowledge_route():
    data  = request.get_json(silent=True) or {}
    actor = (data.get("actor") or "").strip()
    key   = (data.get("key") or "").strip()
    if not actor:
        return jsonify({"error": "actor is required"}), 400
    if not _KNOWLEDGE_KEY_RE.match(key):
        return jsonify({"error": "key must be snake_case (lowercase letters, digits, underscores, starting with a letter)"}), 400
    if rdb.sismember(KNOWLEDGE_INDEX, key):
        return jsonify({"error": "key already exists"}), 409
    store_knowledge_entry(rdb, {
        "key":                key,
        "root_cause_pattern": data.get("root_cause_pattern", ""),
        "fix_action":         data.get("fix_action", ""),
        "trigger_waiting_reason": data.get("trigger_waiting_reason", ""),
        "conclusive":         bool(data.get("conclusive", False)),
        "source":             "manual",
        "created_by":         actor,
    })
    _invalidate_retrieval_snapshot()
    return jsonify({"created": True, "key": key}), 201


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
    fields = {
        "root_cause_pattern": data.get("root_cause_pattern", ""),
        "fix_action":         data.get("fix_action", ""),
        "conclusive":         bool(data.get("conclusive", False)),
        "last_modified_by":   actor,
    }
    if "trigger_waiting_reason" in data:
        fields["trigger_waiting_reason"] = data["trigger_waiting_reason"]
    ok = update_knowledge_entry(rdb, key, fields)
    if not ok:
        return jsonify({"error": "not found"}), 404
    _invalidate_retrieval_snapshot()
    return jsonify({"updated": True})


@app.route("/knowledge/<key>", methods=["DELETE"])
def delete_knowledge_route(key):
    result = delete_knowledge_entry(rdb, key)
    if result == "not_found":
        return jsonify({"error": "not found"}), 404
    if result == "has_history":
        return jsonify({"error": "cannot delete: history entries reference this key"}), 409
    _invalidate_retrieval_snapshot()
    return jsonify({"deleted": True})


@app.route("/history", methods=["GET"])
def list_history_route():
    return jsonify({"history": list_all_history_entries(rdb)})


@app.route("/history/<hid>", methods=["PUT"])
def update_history_route(hid):
    data  = request.get_json(silent=True) or {}
    actor = (data.get("actor") or "").strip()
    if not actor:
        return jsonify({"error": "actor is required"}), 400
    fields = {
        "symptom":          data.get("symptom", ""),
        "context_notes":    data.get("context_notes", ""),
        "last_modified_by": actor,
    }
    if "knowledge_key" in data:
        if not rdb.sismember(KNOWLEDGE_INDEX, data["knowledge_key"]):
            return jsonify({"error": "knowledge_key does not exist"}), 400
        fields["knowledge_key"] = data["knowledge_key"]
    if "service" in data:
        fields["service"] = data["service"]
    ok = update_history_entry(rdb, hid, fields)
    if not ok:
        return jsonify({"error": "not found"}), 404
    _invalidate_retrieval_snapshot()
    return jsonify({"updated": True})


@app.route("/history/<hid>", methods=["DELETE"])
def delete_history_route(hid):
    if not delete_history_entry(rdb, hid):
        return jsonify({"error": "not found"}), 404
    _invalidate_retrieval_snapshot()
    return jsonify({"deleted": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002)
