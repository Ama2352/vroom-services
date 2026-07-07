import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock

try:
    import fakeredis
    _FAKE_REDIS = fakeredis.FakeRedis()
except ImportError:
    import pytest; pytest.skip("fakeredis not installed", allow_module_level=True)

with patch("memory.connect", return_value=_FAKE_REDIS), \
     patch("seed.seed_if_empty", return_value=0):
    import app as agent_app

agent_app.rdb = _FAKE_REDIS
agent_app.OPENROUTER_KEY = "fake-key"

import pytest

@pytest.fixture
def client():
    agent_app.app.config["TESTING"] = True
    with agent_app.app.test_client() as c:
        yield c


_FAKE_FACTS = {
    "pods_available": 0, "pods_desired": 1,
    "waiting_reason": "CrashLoopBackOff", "restarts": 5,
    "last_terminated_reason": "", "init_waiting_reason": "",
    "init_last_terminated_reason": "", "init_restarts": 0,
    "log_error": "dial tcp postgres:5432: i/o timeout",
    "event_reason": "BackOff", "event_message": "container failed",
    "event_object": "ride-abc",
}

_FAKE_DIAGNOSIS = {
    "root_cause":   "PostgreSQL unreachable",
    "dev_action":   "Check PostgreSQL pod logs",
    "kubectl_hint": "kubectl get pods -n platform -l app=postgresql",
}


def _fake_bundle(service, namespace):
    return f"service={service} namespace={namespace} rps=0.0 err=8.3% p99=1.2s loki_errors=47"


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_memory_search_empty_store(client):
    _FAKE_REDIS.flushall()
    r = client.get("/memory/search?q=HighErrorRate+ride-service")
    assert r.status_code == 200
    assert r.get_json()["result"] == "no relevant memory found"


def test_memory_search_missing_query(client):
    r = client.get("/memory/search")
    assert r.status_code == 200
    assert "no relevant memory found" in r.get_json()["result"]


def test_investigate_returns_structured_diagnosis(client):
    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.interpret",              return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    assert r.status_code == 200
    body = r.get_json()
    assert body["root_cause"]   == "PostgreSQL unreachable"
    assert body["dev_action"]   == "Check PostgreSQL pod logs"
    assert body["kubectl_hint"] == "kubectl get pods -n platform -l app=postgresql"


def test_investigate_includes_evidence_snippet(client):
    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.interpret",              return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    body = r.get_json()
    assert "evidence_snippet" in body
    assert "Pods:" in body["evidence_snippet"]


def test_investigate_includes_trusted_match_field(client):
    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.find_trusted_match",     return_value=None), \
         patch("app.interpret",              return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    body = r.get_json()
    assert body["trusted_match"] is False
    assert "related_incidents_unconfirmed" in body


def test_investigate_trusted_match_true_omits_related_incidents(client):
    fake_match = {"source": "knowledge", "knowledge_key": "oom",
                  "root_cause_pattern": "OOM", "fix_action": "increase limit", "context_notes": ""}
    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.find_trusted_match",     return_value=fake_match), \
         patch("app.interpret",              return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    body = r.get_json()
    assert body["trusted_match"] is True
    assert "related_incidents_unconfirmed" not in body
    assert "memory_hits" not in body


def test_investigate_stores_incident_and_returns_incident_id(client):
    _FAKE_REDIS.flushall()
    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.find_trusted_match",     return_value=None), \
         patch("app.interpret",              return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    body = r.get_json()
    assert "incident_id" in body and body["incident_id"]
    assert _FAKE_REDIS.scard("incidents:index") == 1


def test_investigate_records_step_events_in_timeline(client):
    import memory
    _FAKE_REDIS.flushall()
    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.find_trusted_match",     return_value=None), \
         patch("app.interpret",              return_value=dict(_FAKE_DIAGNOSIS)), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    iid = r.get_json()["incident_id"]
    timeline = memory.get_incident_timeline(_FAKE_REDIS, iid)
    step_names = [e["name"] for e in timeline if e.get("type") == "step"]
    # interpret() is mocked here without a _step_log, so only app.py's own stages appear —
    # llm_phase1/quality_check/llm_refine are covered separately by Task 9's TestStepLog and
    # by Task 11's live manual verification.
    assert step_names == [
        "collect_diagnostics", "replicaset_diff", "dependency_chase",
        "trusted_match_check", "record_incident",
    ]
    for entry in timeline:
        if entry.get("type") == "step":
            assert "started_at" in entry and "finished_at" in entry
            assert "duration_ms" in entry
            assert "metadata" in entry


def test_investigate_step_log_not_in_response_body(client):
    fake_diagnosis_with_steps = {**_FAKE_DIAGNOSIS, "_step_log": [
        {"type": "step", "name": "llm_phase1", "started_at": 0, "finished_at": 0,
         "duration_ms": 0, "metadata": {}},
    ]}
    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.interpret",              return_value=fake_diagnosis_with_steps), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    assert "_step_log" not in r.get_json()


# ── /incidents routes ──────────────────────────────────────────────────────────

def _make_fake_incident_kwargs(**overrides):
    base = {
        "alert_name": "A", "service": "ride", "namespace": "vroom-dev",
        "pods_available": 0, "pods_desired": 1, "waiting_reason": "", "last_terminated_reason": "",
        "restarts": 0, "init_waiting_reason": "", "init_last_terminated_reason": "", "init_restarts": 0,
        "log_error": "", "event_reason": "", "event_message": "", "event_object": "",
        "root_cause": "x", "dev_action": "y", "kubectl_hint": "z", "low_confidence": False,
    }
    base.update(overrides)
    return base


def test_list_incidents_empty(client):
    _FAKE_REDIS.flushall()
    r = client.get("/incidents")
    assert r.status_code == 200
    assert r.get_json()["incidents"] == []


def test_list_incidents_filters_by_status(client):
    import memory
    _FAKE_REDIS.flushall()
    iid_open = memory.record_incident_occurrence(_FAKE_REDIS, _make_fake_incident_kwargs())
    r = client.get("/incidents?status=open")
    body = r.get_json()
    assert len(body["incidents"]) == 1
    assert body["incidents"][0]["id"] == iid_open


def test_incidents_latest_returns_none_when_empty(client):
    _FAKE_REDIS.flushall()
    r = client.get("/incidents/latest")
    assert r.get_json()["incident"] is None


def test_incident_detail_includes_timeline_and_pending_suggestion(client):
    import memory
    _FAKE_REDIS.flushall()
    iid = memory.record_incident_occurrence(_FAKE_REDIS, _make_fake_incident_kwargs())
    memory.store_pending_suggestion(_FAKE_REDIS, {
        "service": "ride", "symptom": "s", "proposed_knowledge_key": "k",
        "is_new_knowledge_key": True, "root_cause": "", "fix_action": "",
        "context_notes": "", "source_incident_id": iid,
    })
    r = client.get(f"/incidents/{iid}")
    body = r.get_json()["incident"]
    assert len(body["timeline"]) == 1
    assert body["pending_suggestion"]["source_incident_id"] == iid


def test_incident_detail_missing_returns_404(client):
    assert client.get("/incidents/does-not-exist").status_code == 404


def test_resolve_incident_requires_actor(client):
    import memory
    _FAKE_REDIS.flushall()
    iid = memory.record_incident_occurrence(_FAKE_REDIS, _make_fake_incident_kwargs())
    r = client.post(f"/incidents/{iid}/resolve", data=json.dumps({}), content_type="application/json")
    assert r.status_code == 400


def test_resolve_incident_sets_status(client):
    import memory
    _FAKE_REDIS.flushall()
    iid = memory.record_incident_occurrence(_FAKE_REDIS, _make_fake_incident_kwargs())
    r = client.post(f"/incidents/{iid}/resolve",
                    data=json.dumps({"actor": "Alice"}), content_type="application/json")
    assert r.status_code == 200
    assert memory.get_incident(_FAKE_REDIS, iid)["status"] == "resolved"


def test_resolve_incident_missing_returns_404(client):
    r = client.post("/incidents/does-not-exist/resolve",
                    data=json.dumps({"actor": "Alice"}), content_type="application/json")
    assert r.status_code == 404


def test_investigate_no_old_fields_in_response(client):
    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.find_trusted_match",     return_value=None), \
         patch("app.interpret",              return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    body = r.get_json()
    assert "execution_id"  not in body
    assert "rewoo_steps"   not in body
    assert "remediation"   not in body
    assert "confidence"    not in body
    assert "dev_hint"      not in body
    assert "suggested_command" not in body
    assert "memory_hits"   not in body


def test_investigate_debug_param_returns_facts(client):
    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.interpret",              return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate?debug=true",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    body = r.get_json()
    assert "debug" in body
    assert "facts"  in body["debug"]
    assert "bundle" in body["debug"]
    assert body["debug"]["facts"]["waiting_reason"] == "CrashLoopBackOff"


def test_remediate_endpoint_removed(client):
    r = client.post("/remediate",
        data=json.dumps({"execution_id": "abc", "approved": True}),
        content_type="application/json")
    assert r.status_code == 404


def test_admin_knowledge_routes_removed(client):
    assert client.get("/admin/knowledge").status_code == 404
    assert client.post("/admin/knowledge").status_code == 404
    assert client.post("/admin/knowledge/suggest").status_code == 404


def test_admin_runbook_routes_removed(client):
    assert client.get("/admin/runbook").status_code == 404
    assert client.post("/admin/reseed").status_code == 404


def test_admin_models_hot_swap(client):
    new_models = [
        {"id": "llama-3.3-70b-versatile", "provider": "groq"},
        {"id": "llama-3.1-8b-instant",    "provider": "groq"},
    ]
    r = client.post("/admin/models",
        data=json.dumps(new_models),
        content_type="application/json")
    assert r.status_code == 200
    assert r.get_json()["models"] == new_models
    r2 = client.get("/admin/models")
    assert r2.get_json()["models"] == new_models


def test_admin_models_rejects_string_format(client):
    r = client.post("/admin/models",
        data=json.dumps(["meta-llama/llama-3.3-70b-instruct:free"]),
        content_type="application/json")
    assert r.status_code == 400
    assert "provider" in r.get_json()["error"]


def test_investigate_includes_low_confidence(client):
    _FAKE_DIAGNOSIS_WITH_LC = {**_FAKE_DIAGNOSIS, "low_confidence": False}
    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.interpret",              return_value=_FAKE_DIAGNOSIS_WITH_LC), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    body = r.get_json()
    assert "low_confidence" in body
    assert body["low_confidence"] is False


def test_investigate_forwards_pod_to_interpret(client):
    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.interpret",              return_value=_FAKE_DIAGNOSIS) as mock_interpret, \
         patch("app._reflect_and_store"):
        client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev",
                             "pod": "ride-abc123"}),
            content_type="application/json")
    _, kwargs = mock_interpret.call_args
    assert kwargs.get("pod") == "ride-abc123"


def test_admin_ui_returns_html(client):
    r = client.get("/admin/ui")
    assert r.status_code == 200
    assert "text/html" in r.content_type
    body = r.data.decode()
    assert "Models" in body
    assert "/admin/models" in body


def test_investigate_collects_diagnostics_before_memory_query(client):
    call_order = []

    def fake_collect_diagnostics(service, namespace):
        call_order.append("collect_diagnostics")
        return _FAKE_FACTS

    def fake_find_trusted_match(rdb, facts, query):
        call_order.append("find_trusted_match")
        return None

    def fake_search_memory_items(rdb, query, limit=3):
        call_order.append("search_memory_items")
        return []

    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    side_effect=fake_collect_diagnostics), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.find_trusted_match",     side_effect=fake_find_trusted_match), \
         patch("app.search_memory_items",    side_effect=fake_search_memory_items), \
         patch("app.interpret",              return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")

    assert call_order.index("collect_diagnostics") < call_order.index("find_trusted_match")
    assert call_order.index("find_trusted_match") < call_order.index("search_memory_items")


def test_investigate_query_includes_waiting_reason_and_log_error(client):
    captured = {}

    def fake_find_trusted_match(rdb, facts, query):
        captured["query"] = query
        return None

    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.find_trusted_match",     side_effect=fake_find_trusted_match), \
         patch("app.interpret",              return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")

    assert _FAKE_FACTS["waiting_reason"] in captured["query"]
    assert _FAKE_FACTS["log_error"] in captured["query"]


def test_format_trusted_match_includes_root_cause_and_fix():
    ctx = agent_app._format_trusted_match({
        "source": "knowledge", "knowledge_key": "oom",
        "root_cause_pattern": "Container OOMKilled", "fix_action": "increase memory limit",
        "context_notes": "",
    })
    assert "Container OOMKilled" in ctx
    assert "increase memory limit" in ctx


def test_format_trusted_match_includes_context_notes_when_present():
    ctx = agent_app._format_trusted_match({
        "source": "history", "knowledge_key": "oom",
        "root_cause_pattern": "Container OOMKilled", "fix_action": "increase memory limit",
        "context_notes": "seen during load test",
    })
    assert "seen during load test" in ctx


def test_format_trusted_match_omits_context_notes_when_empty():
    ctx = agent_app._format_trusted_match({
        "source": "knowledge", "knowledge_key": "oom",
        "root_cause_pattern": "Container OOMKilled", "fix_action": "increase memory limit",
        "context_notes": "",
    })
    assert "Notes from a similar past occurrence" not in ctx


def test_reflect_and_store_writes_pending_suggestion_in_mock_mode():
    import memory
    _FAKE_REDIS.flushall()
    with patch.dict(os.environ, {"LLM_MOCK": "true", "LLM_MOCK_SCENARIO": "scale_to_zero"}):
        agent_app._reflect_and_store(
            _FAKE_REDIS,
            {"alert_name": "KubePodNotReady", "service": "ride",
             "root_cause": "scaled to zero", "id": "incident-abc"},
            "kubectl scale deployment/ride -n vroom-dev --replicas=1",
        )
    pending = memory.list_pending_suggestions(_FAKE_REDIS)
    assert len(pending) == 1
    assert pending[0]["source_incident_id"] == "incident-abc"
    assert pending[0]["status"] == "pending"


# ── /pending routes ─────────────────────────────────────────────────────────────

def test_list_pending_defaults_to_pending_status(client):
    import memory
    _FAKE_REDIS.flushall()
    memory.store_pending_suggestion(_FAKE_REDIS, {
        "service": "ride", "symptom": "s", "proposed_knowledge_key": "k",
        "is_new_knowledge_key": True, "root_cause": "", "fix_action": "",
        "context_notes": "", "source_incident_id": "inc-1",
    })
    r = client.get("/pending")
    assert len(r.get_json()["pending"]) == 1


def test_pending_detail_missing_returns_404(client):
    assert client.get("/pending/does-not-exist").status_code == 404


def test_approve_pending_requires_actor_mode_and_key(client):
    import memory
    _FAKE_REDIS.flushall()
    pid = memory.store_pending_suggestion(_FAKE_REDIS, {
        "service": "ride", "symptom": "s", "proposed_knowledge_key": "k",
        "is_new_knowledge_key": False, "root_cause": "", "fix_action": "",
        "context_notes": "", "source_incident_id": "inc-1",
    })
    r = client.post(f"/pending/{pid}/approve", data=json.dumps({}), content_type="application/json")
    assert r.status_code == 400


def test_approve_pending_existing_mode_creates_history(client):
    import memory
    _FAKE_REDIS.flushall()
    memory.store_knowledge_entry(_FAKE_REDIS, {
        "key": "crashloop", "root_cause_pattern": "x", "fix_action": "y",
        "trigger_waiting_reason": "CrashLoopBackOff", "conclusive": False,
        "source": "bootstrap", "created_by": "bootstrap",
    })
    pid = memory.store_pending_suggestion(_FAKE_REDIS, {
        "service": "ride", "symptom": "s", "proposed_knowledge_key": "crashloop",
        "is_new_knowledge_key": False, "root_cause": "", "fix_action": "",
        "context_notes": "", "source_incident_id": "inc-1",
    })
    r = client.post(f"/pending/{pid}/approve", data=json.dumps({
        "actor": "Alice", "mode": "existing", "knowledge_key": "crashloop",
        "symptom": "edited", "context_notes": "notes",
    }), content_type="application/json")
    assert r.status_code == 200
    assert r.get_json()["approved"] is True
    assert memory.get_pending_suggestion(_FAKE_REDIS, pid)["status"] == "approved"


def test_approve_pending_new_mode_saves_trigger_waiting_reason(client):
    import memory
    _FAKE_REDIS.flushall()
    pid = memory.store_pending_suggestion(_FAKE_REDIS, {
        "service": "ride", "symptom": "s", "proposed_knowledge_key": "bad_dependency_address",
        "is_new_knowledge_key": True, "root_cause": "", "fix_action": "",
        "context_notes": "", "source_incident_id": "inc-1",
    })
    r = client.post(f"/pending/{pid}/approve", data=json.dumps({
        "actor": "Alice", "mode": "new", "knowledge_key": "bad_dependency_address",
        "symptom": "s", "context_notes": "notes", "root_cause_pattern": "rc",
        "fix_action": "fix", "conclusive": True, "trigger_waiting_reason": "CrashLoopBackOff",
    }), content_type="application/json")
    assert r.status_code == 200
    entry = memory.get_knowledge_entry(_FAKE_REDIS, "bad_dependency_address")
    assert entry["trigger_waiting_reason"] == "CrashLoopBackOff"


def test_reject_pending_requires_actor(client):
    import memory
    _FAKE_REDIS.flushall()
    pid = memory.store_pending_suggestion(_FAKE_REDIS, {
        "service": "ride", "symptom": "s", "proposed_knowledge_key": "k",
        "is_new_knowledge_key": True, "root_cause": "", "fix_action": "",
        "context_notes": "", "source_incident_id": "inc-1",
    })
    r = client.post(f"/pending/{pid}/reject", data=json.dumps({}), content_type="application/json")
    assert r.status_code == 400


def test_reject_pending_sets_status(client):
    import memory
    _FAKE_REDIS.flushall()
    pid = memory.store_pending_suggestion(_FAKE_REDIS, {
        "service": "ride", "symptom": "s", "proposed_knowledge_key": "k",
        "is_new_knowledge_key": True, "root_cause": "", "fix_action": "",
        "context_notes": "", "source_incident_id": "inc-1",
    })
    r = client.post(f"/pending/{pid}/reject",
                    data=json.dumps({"actor": "Bob"}), content_type="application/json")
    assert r.status_code == 200
    assert memory.get_pending_suggestion(_FAKE_REDIS, pid)["status"] == "rejected"


# ── /knowledge and /history routes ──────────────────────────────────────────────

def test_list_knowledge_includes_history_count(client):
    import memory
    _FAKE_REDIS.flushall()
    memory.store_knowledge_entry(_FAKE_REDIS, {
        "key": "oom", "root_cause_pattern": "x", "fix_action": "y",
        "trigger_waiting_reason": "OOMKilled", "conclusive": True,
        "source": "bootstrap", "created_by": "bootstrap",
    })
    memory.store_history_entry(_FAKE_REDIS, {
        "service": "ride", "knowledge_key": "oom", "symptom": "s",
        "context_notes": "", "source": "bootstrap", "created_by": "bootstrap",
    })
    r = client.get("/knowledge")
    body = r.get_json()["knowledge"]
    assert body[0]["history_count"] == 1


def test_knowledge_detail_missing_returns_404(client):
    assert client.get("/knowledge/does-not-exist").status_code == 404


def test_knowledge_detail_includes_history(client):
    import memory
    _FAKE_REDIS.flushall()
    memory.store_knowledge_entry(_FAKE_REDIS, {
        "key": "oom", "root_cause_pattern": "x", "fix_action": "y",
        "trigger_waiting_reason": "OOMKilled", "conclusive": True,
        "source": "bootstrap", "created_by": "bootstrap",
    })
    memory.store_history_entry(_FAKE_REDIS, {
        "service": "ride", "knowledge_key": "oom", "symptom": "s",
        "context_notes": "", "source": "bootstrap", "created_by": "bootstrap",
    })
    r = client.get("/knowledge/oom")
    body = r.get_json()
    assert body["knowledge"]["key"] == "oom"
    assert len(body["history"]) == 1


def test_update_knowledge_requires_actor(client):
    import memory
    _FAKE_REDIS.flushall()
    memory.store_knowledge_entry(_FAKE_REDIS, {
        "key": "oom", "root_cause_pattern": "x", "fix_action": "y",
        "trigger_waiting_reason": "OOMKilled", "conclusive": True,
        "source": "bootstrap", "created_by": "bootstrap",
    })
    r = client.put("/knowledge/oom", data=json.dumps({"root_cause_pattern": "z"}),
                    content_type="application/json")
    assert r.status_code == 400


def test_update_knowledge_saves_fields(client):
    import memory
    _FAKE_REDIS.flushall()
    memory.store_knowledge_entry(_FAKE_REDIS, {
        "key": "oom", "root_cause_pattern": "x", "fix_action": "y",
        "trigger_waiting_reason": "OOMKilled", "conclusive": True,
        "source": "bootstrap", "created_by": "bootstrap",
    })
    r = client.put("/knowledge/oom", data=json.dumps({
        "actor": "Alice", "root_cause_pattern": "updated", "fix_action": "y", "conclusive": True,
    }), content_type="application/json")
    assert r.status_code == 200
    assert memory.get_knowledge_entry(_FAKE_REDIS, "oom")["root_cause_pattern"] == "updated"


def test_update_knowledge_saves_trigger_waiting_reason(client):
    import memory
    _FAKE_REDIS.flushall()
    memory.store_knowledge_entry(_FAKE_REDIS, {
        "key": "oom", "root_cause_pattern": "x", "fix_action": "y",
        "trigger_waiting_reason": "", "conclusive": True,
        "source": "bootstrap", "created_by": "bootstrap",
    })
    r = client.put("/knowledge/oom", data=json.dumps({
        "actor": "Alice", "root_cause_pattern": "x", "fix_action": "y",
        "conclusive": True, "trigger_waiting_reason": "OOMKilled",
    }), content_type="application/json")
    assert r.status_code == 200
    assert memory.get_knowledge_entry(_FAKE_REDIS, "oom")["trigger_waiting_reason"] == "OOMKilled"


def test_delete_knowledge_refused_when_history_exists(client):
    import memory
    _FAKE_REDIS.flushall()
    memory.store_knowledge_entry(_FAKE_REDIS, {
        "key": "oom", "root_cause_pattern": "x", "fix_action": "y",
        "trigger_waiting_reason": "OOMKilled", "conclusive": True,
        "source": "bootstrap", "created_by": "bootstrap",
    })
    memory.store_history_entry(_FAKE_REDIS, {
        "service": "ride", "knowledge_key": "oom", "symptom": "s",
        "context_notes": "", "source": "bootstrap", "created_by": "bootstrap",
    })
    r = client.delete("/knowledge/oom")
    assert r.status_code == 409


def test_delete_knowledge_missing_returns_404(client):
    assert client.delete("/knowledge/does-not-exist").status_code == 404


def test_update_history_requires_actor(client):
    import memory
    _FAKE_REDIS.flushall()
    hid = memory.store_history_entry(_FAKE_REDIS, {
        "service": "ride", "knowledge_key": "oom", "symptom": "s",
        "context_notes": "", "source": "bootstrap", "created_by": "bootstrap",
    })
    r = client.put(f"/history/{hid}", data=json.dumps({"symptom": "x"}),
                    content_type="application/json")
    assert r.status_code == 400


def test_update_history_saves_fields(client):
    import memory
    _FAKE_REDIS.flushall()
    hid = memory.store_history_entry(_FAKE_REDIS, {
        "service": "ride", "knowledge_key": "oom", "symptom": "s",
        "context_notes": "", "source": "bootstrap", "created_by": "bootstrap",
    })
    r = client.put(f"/history/{hid}", data=json.dumps({
        "actor": "Bob", "symptom": "updated", "context_notes": "n",
    }), content_type="application/json")
    assert r.status_code == 200
    assert memory.get_history_entry(_FAKE_REDIS, hid)["symptom"] == "updated"


def test_delete_history_removes_entry(client):
    import memory
    _FAKE_REDIS.flushall()
    hid = memory.store_history_entry(_FAKE_REDIS, {
        "service": "ride", "knowledge_key": "oom", "symptom": "s",
        "context_notes": "", "source": "bootstrap", "created_by": "bootstrap",
    })
    r = client.delete(f"/history/{hid}")
    assert r.status_code == 200
    assert memory.get_history_entry(_FAKE_REDIS, hid) is None


def test_delete_history_missing_returns_404(client):
    assert client.delete("/history/does-not-exist").status_code == 404
