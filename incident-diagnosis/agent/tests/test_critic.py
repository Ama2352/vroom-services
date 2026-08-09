import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from critic import parse_critic_output, run_semantic_critic
from tests.test_validation import DLQ_CHAIN, VALID_DRAFT


def test_critic_requires_strict_json():
    assert parse_critic_output("not json").status == "invalid"


def test_critic_outage_is_not_a_pass():
    result = run_semantic_critic(DLQ_CHAIN, VALID_DRAFT, _llm=lambda *_: "")
    assert result.status == "unavailable"
    assert result.passed is False


def test_critic_requests_the_strict_verdict_contract():
    def contract_aware_llm(prompt, temperature=0.0):
        if ('Return ONLY JSON' in prompt
                and '"verdict":"pass"' in prompt
                and '"verdict":"fail"' in prompt
                and '"issues"' in prompt):
            return '{"verdict":"pass","issues":[]}'
        return "The diagnosis appears supported."

    result = run_semantic_critic(DLQ_CHAIN, VALID_DRAFT, _llm=contract_aware_llm)

    assert result.passed is True
    assert result.status == "passed"


def test_critic_prompt_is_bounded_and_excludes_raw_source_patches():
    chain = {
        **DLQ_CHAIN,
        "causal_context": [{
            "id": "change:source",
            "role": "causal_context",
            "status": "available",
            "source_path": "provenance",
            "reason": "source revision",
            "payload": {
                "service": "dispatch-service",
                "causal_status": {
                    "status": "causal_candidate",
                    "reason_codes": ["exact_failure_identifier"],
                    "matched_identifiers": ["Payment.Completed.v3"],
                },
                "dual": {
                    "service_source": {
                        "status": "found",
                        "commit": {"sha": "abc123", "message": "change contract"},
                        "changed_files": [{"path": "services/dispatch/consumer.go", "patch": "x" * 100_000}],
                    },
                },
            },
        }],
    }
    captured = {}

    def critic_llm(prompt, temperature=0.0):
        captured["prompt"] = prompt
        return json.dumps({"verdict": "pass", "issues": []})

    result = run_semantic_critic(chain, VALID_DRAFT, _llm=critic_llm)

    assert result.passed is True
    assert len(captured["prompt"].encode()) < 16_000
    assert "changed_files" not in captured["prompt"]
    assert "Payment.Completed.v3" in captured["prompt"]
