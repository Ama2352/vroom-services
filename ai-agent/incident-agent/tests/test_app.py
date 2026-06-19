import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock

try:
    import fakeredis
    _FAKE_REDIS = fakeredis.FakeRedis()
except ImportError:
    import pytest; pytest.skip("fakeredis not installed", allow_module_level=True)

# Patch redis connection before importing app
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


def _fake_bundle(service, namespace):
    return f"service={service} namespace={namespace} rps=12.4 err=8.3% p99=1.2s loki_errors=47"


def _fake_loop(alert, call_tool_fn, api_key, **kw):
    return {
        "root_cause": "dispatch stale cursor",
        "confidence": "HIGH",
        "remediation": {
            "tool": "restart_deployment",
            "args": {"deployment": "dispatch-service", "namespace": "vroom-dev"},
            "justification": "safe restart",
        },
        "investigation_steps": [{"action": "get_pods(namespace=vroom-dev)", "observation": "running"}],
    }


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


def test_investigate_returns_diagnosis(client):
    with patch("app.collect_bundle", side_effect=_fake_bundle), \
         patch("app.run_react_loop", side_effect=_fake_loop):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "HighErrorRate", "service": "ride-service",
                             "severity": "warning", "namespace": "vroom-dev"}),
            content_type="application/json")
    assert r.status_code == 200
    body = r.get_json()
    assert body["confidence"] == "HIGH"
    assert body["remediation"]["tool"] == "restart_deployment"
    assert "execution_id" in body


def test_investigate_stores_pending_in_redis(client):
    with patch("app.collect_bundle", side_effect=_fake_bundle), \
         patch("app.run_react_loop", side_effect=_fake_loop):
        r = client.post("/investigate",
            data=json.dumps({"alert_name": "HighErrorRate", "service": "ride-service",
                             "severity": "warning", "namespace": "vroom-dev"}),
            content_type="application/json")
    eid = r.get_json()["execution_id"]
    assert _FAKE_REDIS.exists(f"pending:{eid}")


def test_remediate_unknown_execution_id(client):
    r = client.post("/remediate",
        data=json.dumps({"execution_id": "nonexistent", "approved": True}),
        content_type="application/json")
    assert r.status_code == 404


def test_remediate_skipped_when_not_approved(client):
    # First: create a pending execution
    with patch("app.collect_bundle", side_effect=_fake_bundle), \
         patch("app.run_react_loop", side_effect=_fake_loop):
        inv = client.post("/investigate",
            data=json.dumps({"alert_name": "HighErrorRate", "service": "ride-service",
                             "severity": "warning", "namespace": "vroom-dev"}),
            content_type="application/json").get_json()

    r = client.post("/remediate",
        data=json.dumps({"execution_id": inv["execution_id"], "approved": False}),
        content_type="application/json")
    assert r.status_code == 200
    assert r.get_json()["outcome"] == "skipped"


def test_remediate_approved_stores_incident_memory(client):
    _FAKE_REDIS.flushall()

    with patch("app.collect_bundle", side_effect=_fake_bundle), \
         patch("app.run_react_loop", side_effect=_fake_loop):
        inv = client.post("/investigate",
            data=json.dumps({"alert_name": "HighErrorRate", "service": "ride-service",
                             "severity": "warning", "namespace": "vroom-dev"}),
            content_type="application/json").get_json()

    mock_exec_resp = MagicMock()
    mock_exec_resp.status_code = 200
    mock_exec_resp.json.return_value = {"stdout": "deployment.apps/dispatch-service restarted", "returncode": 0}

    with patch("requests.post", return_value=mock_exec_resp), \
         patch("app._interpret", return_value="Pod restarted. Monitor error rate."):
        r = client.post("/remediate",
            data=json.dumps({"execution_id": inv["execution_id"], "approved": True}),
            content_type="application/json")

    assert r.status_code == 200
    assert r.get_json()["outcome"] == "resolved"
    # Incident should now be in memory
    assert _FAKE_REDIS.scard("incidents:index") > 0
