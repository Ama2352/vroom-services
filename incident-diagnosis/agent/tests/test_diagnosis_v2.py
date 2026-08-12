from diagnosis_v2 import build_generation_prompt, validate_diagnosis_v2, finalize_diagnosis_v2


CURRENT = {
    "evidence": [
        {"id": "log:selected", "label": "Structured log", "value": "unknown event type Trip.Requested.v2"},
        {"id": "trace:selected", "label": "Trace", "value": "dispatch.consume.Trip.Requested.v2"},
    ]
}


def test_prompt_separates_current_evidence_and_advisory_cases():
    prompt = build_generation_prompt(CURRENT, [{
        "knowledge_key": "unsupported_event_contract",
        "diagnosis_cause": "Producer and consumer contracts differ.",
        "remediation": "Align the contracts.",
        "evidence_text": "log_error: unsupported event",
    }])
    assert "CURRENT EVIDENCE - citable" in prompt
    assert "NEAREST APPROVED EXAMPLES - advisory, not evidence" in prompt
    assert "unsupported_event_contract" in prompt


def test_failed_diagnosis_clears_cause_and_keeps_grounded_hypothesis():
    draft = {
        "diagnosis_cause": "Producer and consumer contracts differ.",
        "hypothesis": "The producer and consumer may use different contract versions.",
        "hypothesis_evidence_refs": ["log:selected"],
        "recommended_action": {"kind": "remediation", "summary": "Align contracts."},
        "evidence_analysis": {"logs": "The consumer rejected Trip.Requested.v2."},
    }
    final = finalize_diagnosis_v2(draft, CURRENT, accepted=False)
    assert final["diagnosis_cause"] is None
    assert final["recommended_action"]["kind"] == "investigation"
    assert final["hypothesis"]


def test_validator_rejects_candidate_only_citation():
    draft = {
        "incident_summary": "The service is failing.",
        "diagnosis_cause": "The approved case proves this cause.",
        "hypothesis": None,
        "recommended_action": {"kind": "remediation", "summary": "Apply the case fix."},
        "evidence_refs": ["knowledge:unsupported_event_contract"],
        "hypothesis_evidence_refs": [],
    }
    result = validate_diagnosis_v2(draft, CURRENT)
    assert not result.passed
    assert "unknown_evidence_reference" in result.issues
