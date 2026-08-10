from finalization import finalize_diagnosis


def test_rejected_answer_becomes_a_safe_investigation_fallback():
    final = finalize_diagnosis(
        {"acceptance_status": "rejected_after_refine"},
        {"evidence": []}, "vroom-dev", "dispatch-service",
    )

    assert final["low_confidence"] is True
    assert final["kubectl_hint"] == "kubectl get pods -n vroom-dev -l app=dispatch-service"
