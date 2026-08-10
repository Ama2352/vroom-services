import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from finalization import finalize_diagnosis
from tests.test_validation import DLQ_CHAIN


def test_rejected_answer_discards_generated_action_and_placeholder_command():
    rejected = {
        "root_cause": "unsupported guess",
        "dev_action": "delete the pod",
        "kubectl_hint": "kubectl logs <dispatch-service-pod-name>",
        "acceptance_status": "rejected_after_refine",
        "_evaluation": {"hard": "failed", "semantic": "failed"},
    }

    final = finalize_diagnosis(rejected, DLQ_CHAIN, "vroom-dev", "dispatch-service")

    assert final["root_cause"].startswith("unknown event type")
    assert final["dev_action"] == "Do not run a remediation command until the diagnosis is reviewed."
    assert final["kubectl_hint"] == "kubectl get pods -n vroom-dev -l app=dispatch-service"
    assert final["diagnosis_decision"]["status"] == "accepted_with_unproven_attribution"
    assert final["causal_chain_summary"] == {
        "incident_kind": "dlq",
        "trigger_ids": ["metric:dlq_events"],
        "primary_ids": ["log:evt-42", "trace:" + "a" * 32],
        "causal_context_ids": ["change:abc1234"],
        "contradiction_ids": [],
    }


def test_rejected_generation_can_publish_confirmed_mechanism_with_unproven_attribution():
    rejected = {
        "root_cause": "dispatch has a bad deployment",
        "dev_action": "rollback the deployment",
        "kubectl_hint": "kubectl rollout undo deployment/dispatch-service",
        "acceptance_status": "rejected_after_refine",
        "_evaluation": {"phase1": {"semantic_critic": {"status": "failed"}}},
    }

    chain = {**DLQ_CHAIN, "causal_context": [{"id": "change:recent", "payload": {"causal_status": {"status": "recent_context"}}}]}
    final = finalize_diagnosis(rejected, chain, "vroom-dev", "dispatch-service")

    assert final["failure_status"] == "confirmed"
    assert final["mechanism_status"] == "confirmed"
    assert final["attribution_status"] == "unproven"
    assert final["diagnosis_decision"]["status"] == "accepted_with_unproven_attribution"
    assert final["diagnosis_decision"]["published_generated_answer"] is False
    assert final["diagnosis_decision"]["published_operator_diagnosis"] is True
    assert "Trip.Requested.v2" in final["root_cause"]
    assert "rollback" not in final["dev_action"].lower()
