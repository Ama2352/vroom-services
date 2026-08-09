import sys
import json
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
    "expected_retrieval_mode": "none",
    "expected_acceptance_status": "accepted",
}


def test_score_case_uses_frozen_expectations_not_critic_verdict():
    diagnosis = {"root_cause": "readiness probe caused this", "dev_action": "rollback", "critic": {"verdict": "pass"}}
    score = score_case(EVENT_CASE, diagnosis)
    assert score["unsupported_claim_free"] is False
    assert score["passed"] is False


def test_score_case_rejects_wrong_retrieval_or_acceptance_contract():
    diagnosis = {
        "root_cause": "Trip.Requested.v2", "dev_action": "rollback",
        "evidence_refs": ["metric:dlq_events"], "low_confidence": False,
        "retrieval_mode": "reranked_advisory", "acceptance_status": "rejected_after_refine",
    }

    score = score_case(EVENT_CASE, diagnosis)

    assert score["retrieval_mode_matches"] is False
    assert score["acceptance_status_matches"] is False
    assert score["passed"] is False


def test_frozen_cases_declare_retrieval_and_acceptance_expectations():
    fixtures = json.loads((Path(__file__).parents[1] / "evaluation" / "fixtures" / "diagnosis_cases.json").read_text())
    required = {"expected_retrieval_mode", "expected_acceptance_status", "required_evidence_refs", "forbidden_claims"}
    assert all(required <= case.keys() for case in fixtures)
