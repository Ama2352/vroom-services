import json

from critic import run_semantic_critic


def test_critic_receives_only_flat_labelled_evidence():
    captured = {}

    def llm(prompt, temperature=0.0):
        captured["prompt"] = prompt
        return json.dumps({"verdict": "pass", "issues": []})

    result = run_semantic_critic(
        {"evidence": [{"id": "fact:log.message", "label": "log.message", "value": "unknown event type Trip.Requested.v2"}]},
        {"root_cause": "unknown event type Trip.Requested.v2", "dev_action": "inspect consumer", "kubectl_hint": "kubectl get pods", "evidence_refs": ["fact:log.message"]},
        _llm=llm,
    )

    assert result.passed
    assert "Trip.Requested.v2" in captured["prompt"]
    assert "causal_context" not in captured["prompt"]
