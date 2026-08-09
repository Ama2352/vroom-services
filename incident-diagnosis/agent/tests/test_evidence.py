import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from evidence import build_evidence_chain, resolve_incident_kind


DLQ_BUNDLE = {
    "impact": {"triggering_metric": {"name": "dlq_events", "value": 1}},
    "log_evidence": {
        "status": "found", "event_id": "evt-42", "message": "unknown event type",
        "trace_id": "a" * 32, "service": "dispatch-service", "namespace": "vroom-dev",
    },
    "trace_handoff": {"status": "correlated", "trace_id": "a" * 32},
    "template_diff": {"env_changed": True, "env_diff": [{"key": "EVENT_CONTRACT_VERSION", "old_value": "v1", "new_value": "v2"}]},
    "provenance": {"classification": "gitops-commit", "commit": {"sha": "abc1234"}},
    "dlq_state": {"count": 1},
    "k8s_state": {"pods_available": 1, "pods_desired": 1},
    "k8s_event": {"id": "readiness", "reason": "Unhealthy"},
    "dependency": {"name": "redis", "pods_available": 1, "pods_desired": 1},
}


def test_dlq_policy_keeps_readiness_event_secondary():
    alert = {"incident_kind": "dlq", "alert_name": "DLQEventsDetected"}
    chain = build_evidence_chain(alert, DLQ_BUNDLE)
    assert chain["trigger"][0]["id"] == "metric:dlq_events"
    assert {item["id"] for item in chain["primary"]} == {"log:evt-42", "trace:" + "a" * 32}
    assert chain["secondary"][0]["id"] == "k8s:event:readiness"


def test_alert_name_is_compatibility_fallback():
    assert resolve_incident_kind({"alert_name": "DLQEventsDetected"}) == "dlq"


def test_status_only_failures_are_not_promoted_to_positive_evidence():
    chain = build_evidence_chain({"alert_name": "GenericAlert"}, {
        "impact": {"status": "no_data"},
        "log_evidence": {"status": "no_match"},
        "trace_handoff": {"status": "no_trace_id"},
        "provenance": {},
        "k8s_state": {"pods_available": 1, "pods_desired": 1},
        "k8s_event": {"id": "", "reason": "", "message": ""},
    })

    assert chain["trigger"] == []
    assert chain["primary"] == []
    assert {item["id"] for item in chain["secondary"]} == {"k8s_state"}
