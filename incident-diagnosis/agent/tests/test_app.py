import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["MAX_CHANGE_AGE_SECONDS"] = "999999999"

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
from retrieval.models import RetrievalDocument, RetrievalResult
from github_client import GitHubResult


def test_dual_provenance_promotes_exact_source_change_without_contract_field_rule():
    deployment = {
        "spec": {"template": {"spec": {"containers": [
            {"image": "ghcr.io/example/ride:v1.0.0-build.z.114.dc213686"},
        ]}}},
    }
    services_client = MagicMock()
    services_client.get_commit.return_value = GitHubResult("available", {
        "sha": "dc213686abcd",
        "commit": {"message": "feat: producer", "author": {"name": "Dev", "date": "2026-08-05T10:00:00Z"}},
        "files": [{"filename": "services/ride/producer.go", "patch": "+ Payment.Completed.v3"}],
    })
    clients = MagicMock(services=services_client)
    gitops = {
        "status": "found", "classification": "gitops-commit", "changed_at": "2026-08-05T10:00:00Z",
        "env_diff": [{"key": "PRODUCER_MODE", "old_value": "legacy", "new_value": "strict"}],
    }
    trace = {"status": "correlated", "involved_services": ["ride-service", "dispatch-service"], "error_message": "unknown event type Payment.Completed.v3"}
    log = {"status": "found", "service": "dispatch-service", "message": "unknown event type Payment.Completed.v3"}

    with patch("app.collect_workload_deployment", return_value=deployment), \
         patch("app.collect_gitops_deployed_change", return_value=gitops), \
         patch.object(agent_app, "_repository_clients", clients):
        result = agent_app._collect_dual_provenance(
            "ride-service", "vroom-dev",
            {"env_diff": [{"key": "PRODUCER_MODE", "old_value": "legacy", "new_value": "strict"}]},
            log, trace, "2026-08-07T10:00:00Z",
        )

    assert result["causal_status"]["status"] == "causal_candidate"
    assert result["dual"]["service_source"]["commit"]["sha"] == "dc213686abcd"
    assert "Payment.Completed.v3" in result["causal_status"]["matched_identifiers"]


def _none_retrieval():
    return RetrievalResult.none(corpus_version=1)


def _exact_retrieval():
    return RetrievalResult.exact_conclusive(RetrievalDocument(
        source="knowledge", source_id="oom", knowledge_key="oom",
        trigger="CrashLoopBackOff", conclusive=True,
        root_cause_pattern="OOM", fix_action="increase limit",
        document_text="CrashLoopBackOff OOM", context_notes="",
    ), corpus_version=1)

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
         patch("app.retrieval_service.retrieve", return_value=_none_retrieval()), \
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
         patch("app.retrieval_service.retrieve", return_value=_exact_retrieval()), \
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
         patch("app.retrieval_service.retrieve", return_value=_none_retrieval()), \
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
         patch("app.retrieval_service.retrieve", return_value=_none_retrieval()), \
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
        "collect_diagnostics", "replicaset_diff", "dependency_chase", "provenance_lookup",
        "routing", "evidence_chain", "trusted_match_check", "record_incident",
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
         patch("app.retrieval_service.retrieve", return_value=_none_retrieval()), \
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
    assert body["low_confidence"] is True


def test_investigate_uses_evidence_confidence_for_low_confidence(client):
    with patch("app.collect_bundle", side_effect=_fake_bundle), \
         patch("app.collect_diagnostics", return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency", return_value=None), \
         patch("app.interpret", return_value={**_FAKE_DIAGNOSIS, "low_confidence": False}), \
         patch("app.assess_confidence", return_value={"level": "unknown", "reasons": [], "missing_evidence": []}), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady", "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    assert r.get_json()["low_confidence"] is True


def test_investigate_passes_dlq_alert_metric_into_impact_collection(client):
    impact = {"status": "available", "window": "5m", "request_rate": None,
              "error_rate_percent": None, "p99_seconds": None, "errors": [],
              "triggering_metric": {"name": "DLQ events", "value": 1.0, "threshold": 0.0}}
    with patch("app.collect_bundle", side_effect=_fake_bundle), \
         patch("app.collect_diagnostics", return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency", return_value=None), \
         patch("app.collect_impact", return_value=impact) as collect_impact, \
         patch("app.collect_log_evidence", return_value={"status": "found", "trace_id": "a" * 32}), \
         patch("app.correlate_trace", return_value={"status": "correlated"}), \
         patch("app.interpret", return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate", data=json.dumps({
            "alert_name": "DLQEventsDetected", "service": "dispatch-service", "namespace": "vroom-dev",
            "starts_at": "2026-08-04T16:14:40Z", "metric_value": 1, "threshold": 0,
        }), content_type="application/json")
    assert collect_impact.call_args.kwargs["alert"]["metric_value"] == 1.0
    assert r.get_json()["diagnosis_confidence"]["level"] == "high"
    assert r.get_json()["low_confidence"] is False


def test_dlq_investigation_selects_generic_causal_provenance_for_upstream_trace_service(client):
    calls = []
    captured = {}
    ride_diff = {
        "changed_at": "2026-08-06T10:30:00Z",
        "env_changed": True,
        "env_diff": [{"key": "EVENT_CONTRACT_VERSION", "old_value": "v1", "new_value": "v2"}],
    }

    def collect_change(service, namespace):
        calls.append(("change", service, namespace))
        return ride_diff if service == "ride-service" else None

    def collect_dual(service, namespace, template_diff, log_evidence, trace_handoff, alert_started_at):
        calls.append(("dual_provenance", service, namespace))
        assert template_diff["service"] == "ride-service"
        return {
            "classification": "gitops-commit",
            "service": service,
            "causal_status": {
                "status": "causal_candidate",
                "reason_codes": ["deployed_identity", "exact_failure_identifier"],
                "matched_identifiers": ["Trip.Requested.v2"],
            },
        }

    def retrieve(alert_name, facts, routing=None):
        captured["routing"] = routing
        return _none_retrieval()

    with patch("app.collect_bundle", side_effect=_fake_bundle), \
         patch("app.collect_diagnostics", return_value=_FAKE_FACTS), \
         patch("app.collect_impact", return_value={"status": "available", "errors": []}), \
         patch("app.collect_log_evidence", return_value={
             "status": "found", "service": "dispatch-service", "namespace": "vroom-dev",
             "message": 'unknown event type "Trip.Requested.v2"', "trace_id": "a" * 32,
         }), \
         patch("app.correlate_trace", return_value={
             "status": "correlated", "trace_id": "a" * 32,
             "involved_services": ["ride-service", "dispatch-service"],
         }), \
         patch("app.collect_change_evidence", side_effect=collect_change), \
         patch("app.resolve_dependency", return_value=None), \
         patch("app._collect_dual_provenance", side_effect=collect_dual), \
         patch("app.retrieval_service.retrieve", side_effect=retrieve), \
         patch("app.interpret", return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        response = client.post("/investigate?debug=true", data=json.dumps({
            "alert_name": "DLQEventsDetected", "incident_kind": "dlq",
            "service": "dispatch-service", "namespace": "vroom-dev",
            "starts_at": "2026-08-06T10:34:32Z", "metric_value": 1,
        }), content_type="application/json")

    assert response.status_code == 200
    assert calls[0] == ("change", "ride-service", "vroom-dev")
    assert ("dual_provenance", "ride-service", "vroom-dev") in calls
    assert response.get_json()["debug"]["facts"]["template_diff"]["service"] == "ride-service"
    assert response.get_json()["debug"]["facts"]["provenance"]["causal_status"]["status"] == "causal_candidate"
    assert captured["routing"] is not None
    assert captured["routing"].incident_kind == "dlq"
    assert response.get_json()["evidence_chain"] == captured["routing"].evidence_chain
    assert response.get_json()["debug"]["routing"]["primary_signals"]


def test_investigate_high_evidence_confidence_preserves_unconfirmed_cause(client):
    diagnosis = {**_FAKE_DIAGNOSIS,
                 "root_cause": "Insufficient evidence to confirm — observed: PostgreSQL unreachable"}
    with patch("app.collect_bundle", side_effect=_fake_bundle), \
         patch("app.collect_diagnostics", return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency", return_value=None), \
         patch("app.interpret", return_value=diagnosis), \
         patch("app.assess_confidence", return_value={"level": "high", "reasons": [], "missing_evidence": []}), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate", data=json.dumps({
            "alert_name": "DLQEventsDetected", "service": "dispatch-service", "namespace": "vroom-dev",
        }), content_type="application/json")
    assert r.get_json()["root_cause"] == "Insufficient evidence to confirm — observed: PostgreSQL unreachable"


def test_investigate_medium_evidence_confidence_preserves_unconfirmed_cause(client):
    diagnosis = {**_FAKE_DIAGNOSIS,
                 "root_cause": "Insufficient evidence to confirm — observed: PostgreSQL unreachable"}
    with patch("app.collect_bundle", side_effect=_fake_bundle), \
         patch("app.collect_diagnostics", return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency", return_value=None), \
         patch("app.interpret", return_value=diagnosis), \
         patch("app.assess_confidence", return_value={"level": "medium", "reasons": [], "missing_evidence": []}), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate", data=json.dumps({
            "alert_name": "DLQEventsDetected", "service": "dispatch-service", "namespace": "vroom-dev",
        }), content_type="application/json")
    assert r.get_json()["root_cause"] == "Insufficient evidence to confirm — observed: PostgreSQL unreachable"


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


def test_obsolete_admin_ui_is_not_exposed(client):
    r = client.get("/admin/ui")
    assert r.status_code == 404
    assert client.get("/admin/models").status_code == 200


def test_investigate_collects_diagnostics_before_memory_query(client):
    call_order = []

    def fake_collect_diagnostics(service, namespace):
        call_order.append("collect_diagnostics")
        return _FAKE_FACTS

    def fake_retrieve(alert_name, facts, routing=None):
        call_order.append("retrieve")
        return _none_retrieval()

    def fake_search_memory_items(rdb, query, limit=3):
        call_order.append("search_memory_items")
        return []

    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    side_effect=fake_collect_diagnostics), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.retrieval_service.retrieve", side_effect=fake_retrieve), \
         patch("app.search_memory_items",    side_effect=fake_search_memory_items), \
         patch("app.interpret",              return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")

    assert call_order.index("collect_diagnostics") < call_order.index("retrieve")
    assert call_order.index("retrieve") < call_order.index("search_memory_items")


def test_investigate_query_includes_waiting_reason_and_log_error(client):
    captured = {}

    def fake_retrieve(alert_name, facts, routing=None):
        captured["facts"] = facts
        captured["routing"] = routing
        return _none_retrieval()

    with patch("app.collect_bundle",         side_effect=_fake_bundle), \
         patch("app.collect_diagnostics",    return_value=_FAKE_FACTS), \
         patch("app.collect_change_evidence", return_value=None), \
         patch("app.resolve_dependency",      return_value=None), \
         patch("app.retrieval_service.retrieve", side_effect=fake_retrieve), \
         patch("app.interpret",              return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")

    assert captured["facts"]["waiting_reason"] == _FAKE_FACTS["waiting_reason"]
    assert captured["facts"]["log_error"] == _FAKE_FACTS["log_error"]
    assert captured["routing"].incident_kind == "generic"


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


# ── /knowledge POST + /history GET/PUT extensions ─────────────────────────

def test_create_knowledge_requires_actor(client):
    r = client.post("/knowledge",
        data=json.dumps({"key": "new_key", "root_cause_pattern": "x", "fix_action": "y", "conclusive": False}),
        content_type="application/json")
    assert r.status_code == 400


def test_create_knowledge_rejects_bad_key_format(client):
    r = client.post("/knowledge",
        data=json.dumps({"actor": "Alice", "key": "Not-Snake-Case",
                         "root_cause_pattern": "x", "fix_action": "y", "conclusive": False}),
        content_type="application/json")
    assert r.status_code == 400


def test_create_knowledge_rejects_duplicate_key(client):
    import memory
    _FAKE_REDIS.flushall()
    memory.store_knowledge_entry(_FAKE_REDIS, {"key": "dup_key", "root_cause_pattern": "a", "fix_action": "b"})
    r = client.post("/knowledge",
        data=json.dumps({"actor": "Alice", "key": "dup_key",
                         "root_cause_pattern": "x", "fix_action": "y", "conclusive": False}),
        content_type="application/json")
    assert r.status_code == 409


def test_create_knowledge_succeeds(client):
    import memory
    _FAKE_REDIS.flushall()
    r = client.post("/knowledge",
        data=json.dumps({"actor": "Alice", "key": "brand_new_key",
                         "root_cause_pattern": "Something broke", "fix_action": "Fix it", "conclusive": True}),
        content_type="application/json")
    assert r.status_code == 201
    entry = memory.get_knowledge_entry(_FAKE_REDIS, "brand_new_key")
    assert entry["root_cause_pattern"] == "Something broke"
    assert entry["source"] == "manual"
    assert entry["created_by"] == "Alice"
    assert entry["conclusive"] is True


def test_list_history_returns_all_entries(client):
    import memory
    _FAKE_REDIS.flushall()
    memory.store_knowledge_entry(_FAKE_REDIS, {"key": "k1", "root_cause_pattern": "a", "fix_action": "b"})
    memory.store_history_entry(_FAKE_REDIS, {"service": "ride", "knowledge_key": "k1", "symptom": "s"})
    r = client.get("/history")
    assert r.status_code == 200
    body = r.get_json()["history"]
    assert len(body) == 1
    assert body[0]["service"] == "ride"
    assert body[0]["knowledge_key"] == "k1"


def test_update_history_reassigns_knowledge_key(client):
    import memory
    _FAKE_REDIS.flushall()
    memory.store_knowledge_entry(_FAKE_REDIS, {"key": "k1", "root_cause_pattern": "a", "fix_action": "b"})
    memory.store_knowledge_entry(_FAKE_REDIS, {"key": "k2", "root_cause_pattern": "c", "fix_action": "d"})
    hid = memory.store_history_entry(_FAKE_REDIS, {"service": "ride", "knowledge_key": "k1", "symptom": "s"})
    r = client.put(f"/history/{hid}",
        data=json.dumps({"actor": "Alice", "knowledge_key": "k2", "service": "dispatch"}),
        content_type="application/json")
    assert r.status_code == 200
    entry = memory.get_history_entry(_FAKE_REDIS, hid)
    assert entry["knowledge_key"] == "k2"
    assert entry["service"] == "dispatch"


def test_update_history_rejects_nonexistent_knowledge_key(client):
    import memory
    _FAKE_REDIS.flushall()
    memory.store_knowledge_entry(_FAKE_REDIS, {"key": "k1", "root_cause_pattern": "a", "fix_action": "b"})
    hid = memory.store_history_entry(_FAKE_REDIS, {"service": "ride", "knowledge_key": "k1", "symptom": "s"})
    r = client.put(f"/history/{hid}",
        data=json.dumps({"actor": "Alice", "knowledge_key": "does_not_exist"}),
        content_type="application/json")
    assert r.status_code == 400
    entry = memory.get_history_entry(_FAKE_REDIS, hid)
    assert entry["knowledge_key"] == "k1"

