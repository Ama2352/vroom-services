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


def _rs(name, created, image, env):
    return {
        "metadata": {"name": name, "creationTimestamp": created},
        "spec": {"template": {"spec": {"containers": [
            {"image": image, "env": [{"name": k, "value": v} for k, v in env.items()]}
        ]}}},
    }


def test_replicasets_requires_auth(client):
    r = client.get("/tools/replicasets?service=ride&namespace=vroom-dev")
    assert r.status_code == 401


def test_replicasets_invalid_namespace(client):
    r = client.get("/tools/replicasets?service=ride&namespace=../etc", headers=AUTH)
    assert r.status_code == 400


def test_replicasets_invalid_service(client):
    r = client.get("/tools/replicasets?service=../etc&namespace=vroom-dev", headers=AUTH)
    assert r.status_code == 400


def test_replicasets_returns_items(client):
    payload = json.dumps({"items": [
        _rs("ride-old", "2026-07-07T01:00:00Z", "img:v1", {"REDIS_ADDR": "redis:6379"}),
        _rs("ride-new", "2026-07-07T02:00:00Z", "img:v1", {"REDIS_ADDR": "bad-host:6379"}),
    ]})
    with patch("subprocess.run", return_value=_mk(payload)):
        r = client.get("/tools/replicasets?service=ride&namespace=vroom-dev", headers=AUTH)
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert len(items) == 2
    assert items[1]["metadata"]["name"] == "ride-new"


def test_replicasets_returns_at_most_2(client):
    payload = json.dumps({"items": [
        _rs(f"ride-{i}", f"2026-07-07T0{i}:00:00Z", "img:v1", {}) for i in range(5)
    ]})
    with patch("subprocess.run", return_value=_mk(payload)):
        r = client.get("/tools/replicasets?service=ride&namespace=vroom-dev", headers=AUTH)
    assert len(r.get_json()["items"]) == 2


def test_replicasets_empty_on_kubectl_error(client):
    with patch("subprocess.run", return_value=_mk("", returncode=1)):
        r = client.get("/tools/replicasets?service=ride&namespace=vroom-dev", headers=AUTH)
    assert r.status_code == 200
    assert r.get_json()["items"] == []


def test_replicasets_handles_invalid_json(client):
    with patch("subprocess.run", return_value=_mk("not json")):
        r = client.get("/tools/replicasets?service=ride&namespace=vroom-dev", headers=AUTH)
    assert r.status_code == 200
    assert r.get_json()["items"] == []


def test_replicasets_survives_long_kubectl_output(client):
    # Same truncation concern as Task 1 — full ReplicaSet specs (managed fields,
    # resource limits, labels) are reliably >2000 chars for even 2 objects.
    big_env = {f"VAR_{i}": "x" * 100 for i in range(10)}
    payload = json.dumps({"items": [
        _rs("ride-old", "2026-07-07T01:00:00Z", "img:v1", big_env),
        _rs("ride-new", "2026-07-07T02:00:00Z", "img:v1", big_env),
    ]})
    assert len(payload) > 2000
    with patch("subprocess.run", return_value=_mk(payload)):
        r = client.get("/tools/replicasets?service=ride&namespace=vroom-dev", headers=AUTH)
    assert r.status_code == 200
    assert len(r.get_json()["items"]) == 2
