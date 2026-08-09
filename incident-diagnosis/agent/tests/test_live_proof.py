import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from live_proof import build_sanitized_proof


def test_proof_contains_decision_contract_without_secret_bearing_fields():
    incident = {
        "id": "incident-42",
        "alert_name": "DLQEventsDetected",
        "service": "dispatch-service",
        "namespace": "vroom-dev",
        "root_cause": "dispatch rejected Payment.Completed.v3",
        "dev_action": "restore consumer compatibility",
        "kubectl_hint": "kubectl get pods -n vroom-dev -l app=dispatch-service",
        "retrieval_support": {"mode": "none", "accepted": False},
        "diagnosis_decision": {"status": "accepted", "published_generated_answer": True},
        "causal_chain_summary": {"primary_ids": ["log:selected"], "causal_context_ids": ["change:abc"]},
        "timeline": [{"type": "step", "name": "semantic_critic", "duration_ms": 88}],
        "provenance": {"commit": {"sha": "abc123"}, "token": "ghp_private_value"},
        "debug": {"authorization": "Bearer secret-value"},
    }

    proof = build_sanitized_proof(incident)

    assert proof["incident_id"] == "incident-42"
    assert proof["retrieval_mode"] == "none"
    assert proof["decision"]["status"] == "accepted"
    assert proof["causal_evidence_ids"] == ["log:selected", "change:abc"]
    assert "token" not in str(proof).lower()
    assert "secret-value" not in str(proof)
