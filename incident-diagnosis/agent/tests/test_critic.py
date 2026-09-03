from critic import run_semantic_critic


def test_semantic_critic_returns_the_llm_rejection_reason():
    result = run_semantic_critic(
        {"evidence": [{"id": "log-1", "text": "database connections timed out"}]},
        {"diagnosis_cause": "a Kubernetes rollout", "evidence_refs": ["log-1"]},
        generate=lambda _prompt: {"verdict": "fail", "issues": ["cause_not_supported"]},
    )

    assert result.passed is False
    assert result.issues == ["cause_not_supported"]
    assert result.status == "failed"
