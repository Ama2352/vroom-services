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
    with patch("app.collect_bundle",      side_effect=_fake_bundle), \
         patch("app.collect_diagnostics", return_value=_FAKE_FACTS), \
         patch("app.interpret",           return_value=_FAKE_DIAGNOSIS), \
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
    with patch("app.collect_bundle",      side_effect=_fake_bundle), \
         patch("app.collect_diagnostics", return_value=_FAKE_FACTS), \
         patch("app.interpret",           return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    body = r.get_json()
    assert "evidence_snippet" in body
    assert "Pods:" in body["evidence_snippet"]


def test_investigate_includes_memory_hits(client):
    with patch("app.collect_bundle",      side_effect=_fake_bundle), \
         patch("app.collect_diagnostics", return_value=_FAKE_FACTS), \
         patch("app.interpret",           return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    body = r.get_json()
    assert "memory_hits" in body
    assert "incidents" in body["memory_hits"]
    assert "runbook"   in body["memory_hits"]


def test_investigate_stores_incident_in_redis(client):
    _FAKE_REDIS.flushall()
    with patch("app.collect_bundle",      side_effect=_fake_bundle), \
         patch("app.collect_diagnostics", return_value=_FAKE_FACTS), \
         patch("app.interpret",           return_value=_FAKE_DIAGNOSIS), \
         patch("app._reflect_and_store"):
        client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    assert _FAKE_REDIS.scard("incidents:index") > 0


def test_investigate_no_old_fields_in_response(client):
    with patch("app.collect_bundle",      side_effect=_fake_bundle), \
         patch("app.collect_diagnostics", return_value=_FAKE_FACTS), \
         patch("app.interpret",           return_value=_FAKE_DIAGNOSIS), \
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


def test_investigate_debug_param_returns_facts(client):
    with patch("app.collect_bundle",      side_effect=_fake_bundle), \
         patch("app.collect_diagnostics", return_value=_FAKE_FACTS), \
         patch("app.interpret",           return_value=_FAKE_DIAGNOSIS), \
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


def test_admin_runbook_renders_markdown(client):
    import memory as mem_mod
    _FAKE_REDIS.flushall()
    mem_mod.store_runbook_entry(_FAKE_REDIS, {
        "title":       "Deployment scaled to zero",
        "service":     "ride-service",
        "symptom":     "No pods running",
        "root_cause":  "replicas=0",
        "fix_command": "kubectl scale deployment/ride-service -n vroom-dev --replicas=1",
        "source":      "bootstrap",
    })
    r = client.get("/admin/runbook")
    assert r.status_code == 200
    assert r.content_type.startswith("text/plain")
    body = r.data.decode()
    assert "Deployment scaled to zero" in body
    assert "bootstrap" in body


def test_admin_reseed_clears_and_reloads(client):
    import memory as mem_mod
    _FAKE_REDIS.flushall()
    mem_mod.store_runbook_entry(_FAKE_REDIS, {
        "title": "Learned entry", "service": "ride-service",
        "symptom": "test", "root_cause": "test",
        "fix_command": "kubectl test", "source": "learned",
    })
    with patch("app.seed_if_empty", return_value=3) as mock_seed:
        r = client.post("/admin/reseed")
    assert r.status_code == 200
    assert r.get_json()["seeded"] == 3
    mock_seed.assert_called_once()


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
    with patch("app.collect_bundle",      side_effect=_fake_bundle), \
         patch("app.collect_diagnostics", return_value=_FAKE_FACTS), \
         patch("app.interpret",           return_value=_FAKE_DIAGNOSIS_WITH_LC), \
         patch("app._reflect_and_store"):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev"}),
            content_type="application/json")
    body = r.get_json()
    assert "low_confidence" in body
    assert body["low_confidence"] is False


def test_investigate_forwards_pod_to_interpret(client):
    with patch("app.collect_bundle",      side_effect=_fake_bundle), \
         patch("app.collect_diagnostics", return_value=_FAKE_FACTS), \
         patch("app.interpret",           return_value=_FAKE_DIAGNOSIS) as mock_interpret, \
         patch("app._reflect_and_store"):
        client.post("/investigate",
            data=json.dumps({"alert_name": "KubePodNotReady",
                             "service": "ride", "namespace": "vroom-dev",
                             "pod": "ride-abc123"}),
            content_type="application/json")
    _, kwargs = mock_interpret.call_args
    assert kwargs.get("pod") == "ride-abc123"
