import json
from unittest.mock import patch, MagicMock
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app as executor_app

@pytest.fixture
def client():
    executor_app.app.config["TESTING"] = True
    executor_app.BEARER_TOKEN = "test-token"
    with executor_app.app.test_client() as c:
        yield c

AUTH = {"Authorization": "Bearer test-token"}

def mock_kubectl(stdout="ok", returncode=0):
    result = MagicMock()
    result.stdout = stdout
    result.stderr = ""
    result.returncode = returncode
    return result

def test_pods_requires_auth(client):
    r = client.get("/tools/pods?namespace=vroom-dev")
    assert r.status_code == 401

def test_pods_invalid_namespace(client):
    r = client.get("/tools/pods?namespace=../etc", headers=AUTH)
    assert r.status_code == 400

def test_pods_success(client):
    with patch("subprocess.run", return_value=mock_kubectl("NAME READY STATUS\npod-abc 1/1 Running")):
        r = client.get("/tools/pods?namespace=vroom-dev", headers=AUTH)
    assert r.status_code == 200
    assert "Running" in r.get_json()["stdout"]

def test_logs_tail_limit(client):
    r = client.get("/tools/logs?service=ride-service&namespace=vroom-dev&tail=9999", headers=AUTH)
    assert r.status_code == 400

def test_logs_success(client):
    with patch("subprocess.run", return_value=mock_kubectl("log line 1\nlog line 2")):
        r = client.get("/tools/logs?service=ride-service&namespace=vroom-dev&tail=50", headers=AUTH)
    assert r.status_code == 200
    assert "log line" in r.get_json()["stdout"]

def test_restart_rejects_invalid_deployment(client):
    r = client.post("/tools/restart",
        data=json.dumps({"deployment": "../etc", "namespace": "vroom-dev"}),
        content_type="application/json", headers=AUTH)
    assert r.status_code == 400

def test_restart_success(client):
    with patch("subprocess.run", return_value=mock_kubectl("deployment.apps/dispatch-service restarted")):
        r = client.post("/tools/restart",
            data=json.dumps({"deployment": "dispatch-service", "namespace": "vroom-dev"}),
            content_type="application/json", headers=AUTH)
    assert r.status_code == 200
    assert "restarted" in r.get_json()["stdout"]

def test_traces_unavailable_on_timeout(client):
    with patch("requests.get", side_effect=Exception("timeout")):
        r = client.get("/tools/traces?service=ride-service", headers=AUTH)
    assert r.status_code == 200
    assert "unavailable" in r.get_json()["stdout"]

def test_traces_success(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"traces": [
        {"traceID": "abc123", "rootTraceName": "HTTP POST /v1/trips", "durationMs": 1234}
    ]}
    with patch("requests.get", return_value=mock_resp):
        r = client.get("/tools/traces?service=ride-service&error_only=true", headers=AUTH)
    assert r.status_code == 200
    assert "abc123" in r.get_json()["stdout"]

def test_pods_label_selector(client):
    with patch("subprocess.run", return_value=mock_kubectl("NAME READY STATUS\nride-service-abc 1/1 Running")):
        r = client.get("/tools/pods?namespace=vroom-dev&label_selector=app=ride-service", headers=AUTH)
    assert r.status_code == 200
    assert "ride-service" in r.get_json()["stdout"]

def test_pods_invalid_label_selector(client):
    r = client.get("/tools/pods?namespace=vroom-dev&label_selector=../../etc", headers=AUTH)
    assert r.status_code == 400

def test_describe_accepts_name_param(client):
    with patch("subprocess.run", return_value=mock_kubectl("Name: ride-service-abc\nNamespace: vroom-dev")):
        r = client.get("/tools/describe?name=ride-service-abc&namespace=vroom-dev", headers=AUTH)
    assert r.status_code == 200

def test_describe_rejects_missing_pod(client):
    r = client.get("/tools/describe?namespace=vroom-dev", headers=AUTH)
    assert r.status_code == 400

def test_scale_success(client):
    with patch("subprocess.run", return_value=mock_kubectl("deployment.apps/ride-service scaled")):
        r = client.post("/tools/scale",
            data=json.dumps({"deployment": "ride-service", "namespace": "vroom-dev", "replicas": 1}),
            content_type="application/json", headers=AUTH)
    assert r.status_code == 200
    assert "scaled" in r.get_json()["stdout"]

def test_scale_rejects_invalid_replicas(client):
    r = client.post("/tools/scale",
        data=json.dumps({"deployment": "ride-service", "namespace": "vroom-dev", "replicas": 99}),
        content_type="application/json", headers=AUTH)
    assert r.status_code == 400

def test_scale_rejects_invalid_deployment(client):
    r = client.post("/tools/scale",
        data=json.dumps({"deployment": "../etc", "namespace": "vroom-dev", "replicas": 1}),
        content_type="application/json", headers=AUTH)
    assert r.status_code == 400


# ── /tools/traces enrichment ──────────────────────────────────────────────

def _make_search_resp():
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"traces": [
        {"traceID": "abc123", "rootTraceName": "POST /v1/trips", "durationMs": 1250}
    ]}
    return m


def _make_span_resp():
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"data": [{"spans": [
        {
            "spanID": "s1", "parentSpanID": "",
            "operationName": "CreateRide",
            "tags": [],
            "process": {"serviceName": "ride-service"},
        },
        {
            "spanID": "s2", "parentSpanID": "s1",
            "operationName": "DialContext",
            "tags": [{"key": "error", "value": True},
                     {"key": "error.message", "value": "connection refused to postgresql"}],
            "process": {"serviceName": "ride-service"},
        },
    ]}]}
    return m


def test_traces_enriched_with_error_span_detail(client):
    with patch("requests.get", side_effect=[_make_search_resp(), _make_span_resp()]):
        r = client.get("/tools/traces?service=ride-service", headers=AUTH)
    assert r.status_code == 200
    body = r.get_json()
    assert "error span:" in body["stdout"]
    assert "connection refused to postgresql" in body["stdout"]


def test_traces_fallback_to_summary_when_span_fetch_fails(client):
    err_resp = MagicMock()
    err_resp.status_code = 500
    with patch("requests.get", side_effect=[_make_search_resp(), err_resp]):
        r = client.get("/tools/traces?service=ride-service", headers=AUTH)
    assert r.status_code == 200
    body = r.get_json()
    assert "trace_id=abc123" in body["stdout"]
    assert "error span:" not in body["stdout"]
