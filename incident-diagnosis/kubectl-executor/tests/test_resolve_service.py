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


def _mk(stdout="", returncode=0):
    r = MagicMock()
    r.stdout, r.stderr, r.returncode = stdout, "", returncode
    return r


_SAMPLE_SVC_JSON = json.dumps({"items": [
    {"metadata": {"name": "redis", "namespace": "platform"},
     "spec": {"clusterIP": "10.43.10.10"}},
    {"metadata": {"name": "postgres", "namespace": "platform"},
     "spec": {"clusterIP": "10.43.68.150"}},
    {"metadata": {"name": "kubernetes", "namespace": "default"},
     "spec": {"clusterIP": "10.43.0.1"}},
]})


def test_resolve_service_requires_auth(client):
    r = client.get("/tools/resolve-service?ip=10.43.68.150")
    assert r.status_code == 401


def test_resolve_service_rejects_invalid_ip(client):
    r = client.get("/tools/resolve-service?ip=not-an-ip", headers=AUTH)
    assert r.status_code == 400


def test_resolve_service_rejects_ip_with_shell_metacharacters(client):
    r = client.get("/tools/resolve-service?ip=10.43.0.1;rm -rf", headers=AUTH)
    assert r.status_code == 400


def test_resolve_service_finds_match(client):
    with patch("subprocess.run", return_value=_mk(_SAMPLE_SVC_JSON)):
        r = client.get("/tools/resolve-service?ip=10.43.68.150", headers=AUTH)
    assert r.status_code == 200
    body = r.get_json()
    assert body["name"]      == "postgres"
    assert body["namespace"] == "platform"


def test_resolve_service_no_match_returns_empty(client):
    with patch("subprocess.run", return_value=_mk(_SAMPLE_SVC_JSON)):
        r = client.get("/tools/resolve-service?ip=10.99.99.99", headers=AUTH)
    assert r.status_code == 200
    assert r.get_json() == {}


def test_resolve_service_empty_on_kubectl_error(client):
    with patch("subprocess.run", return_value=_mk("", returncode=1)):
        r = client.get("/tools/resolve-service?ip=10.43.68.150", headers=AUTH)
    assert r.status_code == 200
    assert r.get_json() == {"error": ""}


def test_resolve_service_handles_invalid_json(client):
    with patch("subprocess.run", return_value=_mk("not json")):
        r = client.get("/tools/resolve-service?ip=10.43.68.150", headers=AUTH)
    assert r.status_code == 200
    assert "error" in r.get_json()
