from finalization import finalize_diagnosis


def test_rejected_answer_becomes_a_safe_investigation_fallback():
    final = finalize_diagnosis(
        {"acceptance_status": "rejected_after_refine"},
        {"evidence": []}, "vroom-dev", "dispatch-service",
    )

    assert final["low_confidence"] is True
    assert final["kubectl_hint"] == "kubectl get pods -n vroom-dev -l app=dispatch-service"


def test_rejected_answer_keeps_a_cited_unconfirmed_hypothesis():
    final = finalize_diagnosis(
        {
            "acceptance_status": "rejected_after_refine",
            "root_cause": "Insufficient evidence to confirm a safe root cause.",
            "hypothesis": "The log likely indicates dispatch rejects Trip.Requested.v2.",
            "hypothesis_evidence_refs": ["fact:log.message"],
        },
        {"evidence": [{"id": "fact:log.message", "value": "unknown event type Trip.Requested.v2"}]},
        "vroom-dev", "dispatch-service",
    )

    assert final["root_cause"].startswith("Insufficient evidence")
    assert final["hypothesis"] == "The log likely indicates dispatch rejects Trip.Requested.v2."
    assert final["hypothesis_evidence_refs"] == ["fact:log.message"]
