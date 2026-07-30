from dataclasses import replace
from pathlib import Path

from evaluation.baseline import seed_store
from evaluation.bm25_variants import collect_canonical_signals, rank_bm25
from evaluation.fixture_loader import load_cases
from evaluation.models import VariantConfig


FIXTURE_PATH = Path(__file__).parents[1] / "evaluation/fixtures/retrieval_cases.json"
CONFIG = VariantConfig("baseline_plain", "baseline", "plain", threshold=0.0)


def _case(case_id):
    return next(c for c in load_cases(FIXTURE_PATH) if c.id == case_id)


def test_signal_collection_keeps_multiple_observations():
    signals = collect_canonical_signals(_case("ambiguous_conclusive").facts)
    assert "Init:OOMKilled" in signals
    assert "CreateContainerConfigError" in signals


def test_unique_conclusive_signal_returns_exact():
    outcome = rank_bm25(seed_store(), _case("oom_exact"), CONFIG)
    assert outcome.mode == "exact"
    assert outcome.candidates[0].knowledge_key == "oom"


def test_multiple_conclusive_signals_fall_through_as_ambiguous():
    outcome = rank_bm25(seed_store(), _case("ambiguous_conclusive"), CONFIG)
    assert outcome.mode in {"advisory", "none"}
    assert outcome.exact_ambiguous is True


def test_bm25_excludes_zero_overlap():
    outcome = rank_bm25(seed_store(), _case("sparse_no_match"), CONFIG)
    assert outcome.mode == "none"
    assert outcome.candidates == ()


def test_rankings_are_deterministic():
    case = _case("outbox_not_draining")
    first = rank_bm25(seed_store(), case, CONFIG)
    second = rank_bm25(seed_store(), case, CONFIG)
    assert first == second


def test_query_repetition_does_not_change_rank():
    case = _case("outbox_not_draining")
    repeated = replace(
        case,
        facts={
            **case.facts,
            "log_error": " ".join([case.facts["log_error"]] * 3),
        },
    )
    first = rank_bm25(seed_store(), case, CONFIG)
    second = rank_bm25(seed_store(), repeated, CONFIG)
    assert [c.knowledge_key for c in first.candidates] == [
        c.knowledge_key for c in second.candidates
    ]


def test_results_are_collapsed_by_knowledge_key():
    import memory

    rdb = seed_store()
    memory.store_history_entry(rdb, {
        "service": "ride-service",
        "knowledge_key": "outbox_not_draining",
        "symptom": "outbox_events PENDING no PUBLISHED rows",
        "context_notes": "duplicate approved occurrence",
        "source": "learned",
        "created_by": "tester",
    })
    outcome = rank_bm25(rdb, _case("outbox_not_draining"), CONFIG)
    keys = [c.knowledge_key for c in outcome.candidates]
    assert len(keys) == len(set(keys))
