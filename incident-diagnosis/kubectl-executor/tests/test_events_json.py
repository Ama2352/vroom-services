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

_SAMPLE_EVENTS_JSON = json.dumps({
    "items": [
        {
            "type": "Normal",
            "reason": "ScalingReplicaSet",
            "message": "Scaled up",
            "involvedObject": {"name": "ride-abc-xyz"},
            "lastTimestamp": "2026-06-29T10:00:00Z",
        },
        {
            "type": "Warning",
            "reason": "BackOff",
            "message": "Back-off restarting failed container",
            "involvedObject": {"name": "ride-abc-xyz"},
            "lastTimestamp": "2026-06-29T10:01:00Z",
        },
        {
            "type": "Warning",
            "reason": "Failed",
            "message": "Error: ErrImagePull",
            "involvedObject": {"name": "other-svc-pod"},
            "lastTimestamp": "2026-06-29T10:02:00Z",
        },
    ]
})


def _mk(stdout="", returncode=0):
    r = MagicMock()
    r.stdout, r.stderr, r.returncode = stdout, "", returncode
    return r


def test_events_json_requires_auth(client):
    r = client.get("/tools/events-json?namespace=vroom-dev&service=ride")
    assert r.status_code == 401


def test_events_json_invalid_namespace(client):
    r = client.get("/tools/events-json?namespace=../etc&service=ride", headers=AUTH)
    assert r.status_code == 400


def test_events_json_invalid_service(client):
    r = client.get("/tools/events-json?namespace=vroom-dev&service=../etc", headers=AUTH)
    assert r.status_code == 400


def test_events_json_filters_warning_only(client):
    with patch("subprocess.run", return_value=_mk(_SAMPLE_EVENTS_JSON)):
        r = client.get("/tools/events-json?namespace=vroom-dev&service=ride", headers=AUTH)
    assert r.status_code == 200
    events = r.get_json()["events"]
    # "Normal" ScalingReplicaSet is excluded; "other-svc-pod" excluded by prefix
    assert len(events) == 1
    assert events[0]["reason"] == "BackOff"
    assert events[0]["object"] == "ride-abc-xyz"


def test_events_json_filters_by_service_prefix(client):
    with patch("subprocess.run", return_value=_mk(_SAMPLE_EVENTS_JSON)):
        r = client.get("/tools/events-json?namespace=vroom-dev&service=other", headers=AUTH)
    assert r.status_code == 200
    events = r.get_json()["events"]
    assert len(events) == 1
    assert events[0]["object"] == "other-svc-pod"


def test_events_json_returns_max_3(client):
    many = {"items": [
        {
            "type": "Warning", "reason": "BackOff", "message": f"msg{i}",
            "involvedObject": {"name": f"ride-pod-{i}"},
            "lastTimestamp": f"2026-06-29T10:0{i}:00Z",
        }
        for i in range(5)
    ]}
    with patch("subprocess.run", return_value=_mk(json.dumps(many))):
        r = client.get("/tools/events-json?namespace=vroom-dev&service=ride", headers=AUTH)
    assert r.status_code == 200
    assert len(r.get_json()["events"]) <= 3


def test_events_json_returns_empty_on_kubectl_error(client):
    with patch("subprocess.run", return_value=_mk("", returncode=1)):
        r = client.get("/tools/events-json?namespace=vroom-dev&service=ride", headers=AUTH)
    assert r.status_code == 200
    assert r.get_json()["events"] == []


def test_events_json_handles_invalid_json(client):
    with patch("subprocess.run", return_value=_mk("not json")):
        r = client.get("/tools/events-json?namespace=vroom-dev&service=ride", headers=AUTH)
    assert r.status_code == 200
    assert "events" in r.get_json()


def test_events_json_response_has_required_fields(client):
    with patch("subprocess.run", return_value=_mk(_SAMPLE_EVENTS_JSON)):
        r = client.get("/tools/events-json?namespace=vroom-dev&service=ride", headers=AUTH)
    events = r.get_json()["events"]
    if events:
        assert {"reason", "message", "object", "last_seen"}.issubset(events[0].keys())
