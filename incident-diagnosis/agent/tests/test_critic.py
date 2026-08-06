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
