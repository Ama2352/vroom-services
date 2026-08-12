from evidence_projection import normalize_evidence
from investigation_v2 import decide_diagnosis
from retrieval.evidence import EvidenceCandidate, EvidenceRetrievalMode, EvidenceRetrievalResult


def _template():
    return normalize_evidence(
        {"alert_name": "DLQEventsDetected", "service": "dispatch-service", "metric_value": 1.08},
        {"pods_ready": 1, "pods_desired": 1},
        {"status": "found", "message": 'unknown event type "Trip.Requested.v2"'},
        {"status": "correlated", "error_service": "dispatch-service",
         "error_operation": "dispatch.consume.Trip.Requested.v2",
         "error_message": 'unknown event type "Trip.Requested.v2"'},
        {},
    )


def test_advisory_result_keeps_grounded_hypothesis_and_three_examples():
    template = _template()
    candidates = tuple(
        EvidenceCandidate(f"case_{number}", f"example_{number}", f"log_error: event mismatch")
        for number in range(4)
    )
    retrieval = EvidenceRetrievalResult(EvidenceRetrievalMode.NEAREST, candidates)
    result = decide_diagnosis(
        template, retrieval,
        lambda _prompt: {
            "evidence_analysis": {"logs": "The consumer rejected Trip.Requested.v2."},
            "incident_summary": "dispatch-service rejected an event.",
            "diagnosis_cause": None,
            "hypothesis": "The producer and consumer event contracts differ.",
            "recommended_action": {"kind": "investigation", "summary": "Compare the producer and consumer event contracts."},
            "used_knowledge_keys": ["case_0"],
            "evidence_refs": ["log:selected", "trace:selected"],
            "hypothesis_evidence_refs": ["log:selected", "trace:selected"],
        },
    )
    assert result["diagnosis_cause"] is None
    assert result["hypothesis"]
    assert len(result["advisory_examples"]) == 3
    assert "root_cause" not in result


def test_exact_result_reuses_approved_cause_without_llm():
    template = _template()
    retrieval = EvidenceRetrievalResult(EvidenceRetrievalMode.EXACT, (
        EvidenceCandidate("unsupported_event_contract", "example-1", ""),
    ))
    result = decide_diagnosis(
        template, retrieval,
        lambda _prompt: (_ for _ in ()).throw(AssertionError("LLM must not run")),
        knowledge={"diagnosis_cause": "Producer and consumer contracts differ.",
                   "remediation": "Align the event contract versions."},
    )
    assert result["diagnosis_cause"] == "Producer and consumer contracts differ."
    assert result["hypothesis"] is None


def test_advisory_refines_invalid_first_draft_once():
    template = _template()
    retrieval = EvidenceRetrievalResult(EvidenceRetrievalMode.NONE)
    calls = []

    def generate(_prompt):
        calls.append(_prompt)
        if len(calls) == 1:
            return {"incident_summary": "dispatch-service rejected an event.", "evidence_analysis": "bad",
                    "recommended_action": "bad", "evidence_refs": ["log:selected"]}
        return {"incident_summary": "dispatch-service rejected an event.",
                "evidence_analysis": {"logs": "The consumer rejected the event."},
                "hypothesis": "The event contract may differ.",
                "hypothesis_evidence_refs": ["log:selected"],
                "recommended_action": {"kind": "investigation", "summary": "Compare the event contracts."},
                "evidence_refs": ["log:selected"]}

    result = decide_diagnosis(template, retrieval, generate)
    assert len(calls) == 2
    assert result["recommended_action"]["summary"] == "Compare the event contracts."


def test_advisory_never_publishes_a_diagnosis_cause():
    template = _template()
    retrieval = EvidenceRetrievalResult(EvidenceRetrievalMode.NEAREST, (
        EvidenceCandidate("event_contract", "example-1", "event mismatch"),
    ))
    result = decide_diagnosis(template, retrieval, lambda _prompt: {
        "incident_summary": "dispatch-service rejected an event.",
        "evidence_analysis": {"logs": "The consumer rejected the event."},
        "diagnosis_cause": "The event contract is incompatible.",
        "hypothesis": "The event contract may differ.",
        "hypothesis_evidence_refs": ["log:selected"],
        "recommended_action": {"kind": "investigation", "summary": "Compare event contracts."},
        "evidence_refs": ["log:selected"],
    })
    assert result["diagnosis_cause"] is None
