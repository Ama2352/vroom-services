import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from validation import validate_diagnosis


DLQ_CHAIN = {
    "incident_kind": "dlq",
    "required": ["impact.triggering_metric", "log_evidence"],
    "trigger": [{"id": "metric:dlq_events", "payload": {"value": 1}}],
    "primary": [
        {"id": "log:evt-42", "payload": {"message": "unknown event type Trip.Requested.v2", "trace_id": "a" * 32, "service": "dispatch-service", "namespace": "vroom-dev"}},
        {"id": "trace:" + "a" * 32, "payload": {"trace_id": "a" * 32, "status": "correlated"}},
    ],
    "causal_context": [{"id": "change:abc1234", "payload": {"status": "causal_candidate"}}],
}
VALID_DRAFT = {
    "root_cause": "dispatch rejects Trip.Requested.v2 and sends it to the DLQ",
    "dev_action": "rollback EVENT_CONTRACT_VERSION to v1 or deploy a compatible consumer",
    "kubectl_hint": "kubectl get pods -n vroom-dev",
    "evidence_refs": ["metric:dlq_events", "log:evt-42", "trace:" + "a" * 32],
}


def test_validator_rejects_unrelated_readiness_root_cause_for_dlq():
    draft = {"root_cause": "readiness timeout", "dev_action": "increase timeout", "kubectl_hint": "kubectl get pods -n vroom-dev", "evidence_refs": ["k8s:event:readiness"]}
    result = validate_diagnosis(draft, DLQ_CHAIN)
    assert not result.passed
    assert "missing_required_primary_reference" in result.issues


def test_validator_rejects_trace_id_not_in_chain():
    draft = {**VALID_DRAFT, "evidence_refs": ["metric:dlq_events", "log:evt-42", "trace:wrong"]}
    assert "unknown_evidence_reference" in validate_diagnosis(draft, DLQ_CHAIN).issues
