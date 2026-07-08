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

def _mk(stdout="", returncode=0, stderr=""):
    r = MagicMock()
    r.stdout, r.stderr, r.returncode = stdout, stderr, returncode
    return r

def test_deployment_requires_auth(client):
    r = client.get("/tools/deployment?service=ride-service&namespace=vroom-dev")
    assert r.status_code == 401

def test_deployment_rejects_invalid_params(client):
    r1 = client.get("/tools/deployment?service=bad;rm&namespace=vroom-dev", headers=AUTH)
    assert r1.status_code == 400
    r2 = client.get("/tools/deployment?service=ride-service&namespace=bad;rm", headers=AUTH)
    assert r2.status_code == 400

def test_deployment_returns_data(client):
    mock_payload = {"metadata": {"name": "ride-service"}, "status": {"conditions": []}}
    with patch("subprocess.run", return_value=_mk(json.dumps(mock_payload))):
        r = client.get("/tools/deployment?service=ride-service&namespace=vroom-dev", headers=AUTH)
    assert r.status_code == 200
    assert r.get_json() == {"deployment": mock_payload}

def test_deployment_returns_500_on_kubectl_error(client):
    with patch("subprocess.run", return_value=_mk("", returncode=1, stderr="Not found")):
        r = client.get("/tools/deployment?service=ride-service&namespace=vroom-dev", headers=AUTH)
    assert r.status_code == 500
    assert "error" in r.get_json()

def test_deployment_returns_500_on_invalid_json(client):
    with patch("subprocess.run", return_value=_mk("not json")):
        r = client.get("/tools/deployment?service=ride-service&namespace=vroom-dev", headers=AUTH)
    assert r.status_code == 500
    assert "error" in r.get_json()
