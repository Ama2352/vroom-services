import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from evaluation.diagnosis import score_case


EVENT_CASE = {
    "name": "event-contract-regression",
    "expected_claims": ["Trip.Requested.v2"],
    "required_evidence_refs": ["metric:dlq_events"],
    "forbidden_claims": ["readiness probe"],
    "required_action_claims": ["rollback"],
    "expected_low_confidence": False,
}


def test_score_case_uses_frozen_expectations_not_critic_verdict():
    diagnosis = {"root_cause": "readiness probe caused this", "dev_action": "rollback", "critic": {"verdict": "pass"}}
    score = score_case(EVENT_CASE, diagnosis)
    assert score["unsupported_claim_free"] is False
    assert score["passed"] is False
