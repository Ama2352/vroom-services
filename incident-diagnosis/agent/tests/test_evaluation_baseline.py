from evaluation.baseline import (
    _stabilize_seeded_history_ids,
    rank_current_coverage,
    seed_store,
)
from evaluation.fixture_loader import load_cases
from pathlib import Path

import memory


FIXTURE_PATH = Path(__file__).parents[1] / "evaluation/fixtures/retrieval_cases.json"


def _case(case_id):
    return next(c for c in load_cases(FIXTURE_PATH) if c.id == case_id)


def test_current_baseline_preserves_conclusive_short_circuit():
    outcome = rank_current_coverage(seed_store(), _case("oom_exact"))
    assert outcome.mode == "exact"
    assert outcome.candidates[0].knowledge_key == "oom"


def test_current_baseline_exposes_ranked_token_coverage_candidates():
    outcome = rank_current_coverage(seed_store(), _case("outbox_not_draining"))
    assert outcome.mode in {"advisory", "none"}
    assert all(0.0 <= candidate.score <= 1.0 for candidate in outcome.candidates)


def test_current_baseline_excludes_below_half_coverage():
    outcome = rank_current_coverage(seed_store(), _case("tls_no_match"))
    assert all(candidate.score >= 0.5 for candidate in outcome.candidates)


def test_seed_store_is_fully_local():
    rdb = seed_store()
    assert rdb.scard("knowledge:index") >= 8
    assert rdb.scard("history:index") >= 1


def test_seed_store_assigns_stable_unique_history_ids():
    first = memory.list_all_history_entries(seed_store())
    second = memory.list_all_history_entries(seed_store())
    first_ids = sorted(entry["id"] for entry in first)
    second_ids = sorted(entry["id"] for entry in second)
    assert first_ids == second_ids
    assert len(first_ids) == len(set(first_ids)) == len(first)


def test_stable_history_ids_preserve_identical_records_as_distinct():
    import fakeredis

    rdb = fakeredis.FakeRedis()
    duplicate = {
        "knowledge_key": "same",
        "symptom": "same symptom",
        "context_notes": "same context",
    }
    memory.store_history_entry(rdb, duplicate)
    memory.store_history_entry(rdb, duplicate)
    _stabilize_seeded_history_ids(rdb)
    histories = memory.list_all_history_entries(rdb)
    assert len(histories) == 2
    assert len({entry["id"] for entry in histories}) == 2


def test_instrumented_top1_matches_production_find_trusted_match():
    case = _case("outbox_not_draining")
    rdb = seed_store()
    query = memory.build_symptom_text(
        case.alert_name,
        case.facts.get("waiting_reason", ""),
        case.facts.get("log_error", ""),
    )
    production = memory.find_trusted_match(rdb, case.facts, query)
    instrumented = rank_current_coverage(rdb, case)
    if production is None:
        assert instrumented.mode == "none"
    else:
        assert instrumented.candidates[0].knowledge_key == production["knowledge_key"]
