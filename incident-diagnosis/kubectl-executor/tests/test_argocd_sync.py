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


_SYNCED_APP_JSON        = json.dumps({"status": {"sync": {"status": "Synced"}}})
_OUT_OF_SYNC_APP_JSON   = json.dumps({"status": {"sync": {"status": "OutOfSync"}}})


def test_argocd_sync_requires_auth(client):
    r = client.get("/tools/argocd-sync?app=vroom-dev-ride")
    assert r.status_code == 401


def test_argocd_sync_rejects_invalid_app_name(client):
    r = client.get("/tools/argocd-sync?app=bad;rm -rf", headers=AUTH)
    assert r.status_code == 400


def test_argocd_sync_returns_synced(client):
    with patch("subprocess.run", return_value=_mk(_SYNCED_APP_JSON)):
        r = client.get("/tools/argocd-sync?app=vroom-dev-ride", headers=AUTH)
    assert r.status_code == 200
    assert r.get_json() == {"sync_status": "Synced"}


def test_argocd_sync_returns_out_of_sync(client):
    with patch("subprocess.run", return_value=_mk(_OUT_OF_SYNC_APP_JSON)):
        r = client.get("/tools/argocd-sync?app=vroom-dev-ride", headers=AUTH)
    assert r.status_code == 200
    assert r.get_json() == {"sync_status": "OutOfSync"}


def test_argocd_sync_unknown_when_app_not_found(client):
    with patch("subprocess.run", return_value=_mk("", returncode=1)):
        r = client.get("/tools/argocd-sync?app=does-not-exist", headers=AUTH)
    assert r.status_code == 200
    assert r.get_json() == {"sync_status": "Unknown"}


def test_argocd_sync_unknown_on_invalid_json(client):
    with patch("subprocess.run", return_value=_mk("not json")):
        r = client.get("/tools/argocd-sync?app=vroom-dev-ride", headers=AUTH)
    assert r.status_code == 200
    assert r.get_json() == {"sync_status": "Unknown"}


def test_argocd_sync_unknown_on_null_status(client):
    with patch("subprocess.run", return_value=_mk(json.dumps({"status": None}))):
        r = client.get("/tools/argocd-sync?app=vroom-dev-ride", headers=AUTH)
    assert r.status_code == 200
    assert r.get_json() == {"sync_status": "Unknown"}

