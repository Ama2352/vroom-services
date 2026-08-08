import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from routing import route_incident


DLQ_BUNDLE = {
    "impact": {"triggering_metric": {"name": "dlq_events", "value": 1}},
    "log_evidence": {
        "status": "found",
        "event_id": "evt-contract",
        "message": "unknown event type Trip.Requested.v2",
        "operation": "dispatch.consume",
        "service": "dispatch-service",
    },
    "trace_handoff": {
        "status": "correlated",
        "trace_id": "a" * 32,
        "error_operation": "dispatch.consume",
        "error_message": "unknown event type Trip.Requested.v2",
        "involved_services": ["ride-service", "dispatch-service"],
    },
    "template_diff": {
        "env_diff": [
            {
                "key": "EVENT_CONTRACT_VERSION",
                "old_value": "v1",
                "new_value": "v2",
            }
        ]
    },
    "provenance": {
        "classification": "gitops-commit",
        "service": "ride-service",
        "affected_fields": ["env.EVENT_CONTRACT_VERSION"],
    },
    "dlq_state": {"value": 1},
    "k8s_state": {"pods_available": 1, "pods_desired": 1},
    "k8s_event": {
        "id": "readiness",
        "reason": "Unhealthy",
        "message": "Readiness probe timed out",
    },
    "dependency": {
        "name": "redis",
        "pods_available": 1,
        "pods_desired": 1,
    },
}


def test_dlq_route_prioritizes_canonical_log_and_trace():
    decision = route_incident(
        {"incident_kind": "dlq", "alert_name": "DLQEventsDetected"},
        deepcopy(DLQ_BUNDLE),
    )

    assert decision.incident_kind == "dlq"
    assert decision.evidence_chain["primary"][0]["source_path"] == "log_evidence"
    primary = " ".join(decision.primary_signals)
    secondary = " ".join(decision.secondary_signals)
    assert "unknown event type Trip.Requested.v2" in primary
    assert "ride-service" in primary
    assert "dispatch-service" in primary
    assert "Readiness probe timed out" not in primary
    assert "Readiness probe timed out" in secondary


def test_crashloop_route_prioritizes_kubernetes_and_container_error():
    bundle = deepcopy(DLQ_BUNDLE)
    bundle["k8s_state"] = {
        "waiting_reason": "CrashLoopBackOff",
        "last_terminated_reason": "Error",
        "log_error": "panic: failed to initialize",
        "restarts": 7,
    }
    bundle["log_evidence"] = {
        "status": "found",
        "event_id": "evt-crash",
        "message": "panic: failed to initialize",
    }

    decision = route_incident(
        {"incident_kind": "crashloop", "alert_name": "KubePodCrashLooping"},
        bundle,
    )

    primary = " ".join(decision.primary_signals)
    assert "CrashLoopBackOff" in primary
    assert "panic: failed to initialize" in primary
    assert decision.evidence_chain["primary"][0]["source_path"] == "k8s_state"


def test_unknown_kind_uses_generic_route_without_dropping_evidence():
    decision = route_incident(
        {"incident_kind": "new_kind", "alert_name": "UnclassifiedFailure"},
        deepcopy(DLQ_BUNDLE),
    )

    assert decision.incident_kind == "generic"
    assert "generic_fallback" in decision.reason_codes
    assert decision.evidence_chain["incident_kind"] == "generic"
    assert decision.evidence_chain["secondary"]


def test_router_uses_observed_values_without_inventing_a_diagnosis():
    decision = route_incident({"incident_kind": "dlq"}, deepcopy(DLQ_BUNDLE))
    routed_text = " ".join((*decision.primary_signals, *decision.secondary_signals))

    assert "unknown event type Trip.Requested.v2" in routed_text
    assert "ride-service" in routed_text
    assert "dispatch-service" in routed_text
    assert "producer consumer incompatibility" not in routed_text
    assert "rollback ride-service" not in routed_text
