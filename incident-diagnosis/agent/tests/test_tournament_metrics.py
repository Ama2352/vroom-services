import pytest

from evaluation.models import MetricSummary, RankedCandidate, RetrievalCase, RetrievalOutcome
from evaluation.tournament_metrics import (
    candidate_recall_at_8,
    passes_tournament_gate,
    select_recommendation,
    select_score_floor,
    summarize_tournament,
)
from evaluation.tournament_models import OperationalMetrics, SystemEvaluation


def _candidate(key, score=1.0):
    return RankedCandidate(key, score, "knowledge", key, (), "cause", "fix")


@pytest.fixture
def case_factory():
    def make(case_id, keys=(), mode="none", forbidden=(), split="held_out"):
        return RetrievalCase(
            case_id, split, "Alert", {}, tuple(keys), mode, tuple(forbidden), "test"
        )

    return make


@pytest.fixture
def outcome_factory():
    def make(*keys, mode="advisory"):
        return RetrievalOutcome(mode, tuple(_candidate(key, 1.0 - index / 10) for index, key in enumerate(keys)))

    return make


@pytest.fixture
def summary_factory():
    return lambda: MetricSummary(1, 1, 1, 1, 0, 0, 0)


@pytest.fixture
def system_factory(summary_factory):
    def make(name, kind, *, false_positive_rate=0.0, top1=1.0, recall=1.0, passed=True):
        calibration = MetricSummary(10, 10, int(top1 * 10), int(recall * 10), int(false_positive_rate * 10), 0, 0)
        return SystemEvaluation(
            name, kind, calibration, calibration, None, True, passed,
            OperationalMetrics(current_spend_usd=0.0, p95_ms=10.0, peak_rss_mb=10.0),
        )

    return make


def test_summary_reports_mrr_and_correct_abstention(case_factory, outcome_factory):
    cases = (
        case_factory("positive", keys=("right",), mode="advisory"),
        case_factory("negative", mode="none"),
    )
    outcomes = {
        "positive": outcome_factory("wrong", "right"),
        "negative": outcome_factory(),
    }
    summary = summarize_tournament(cases, outcomes)
    assert summary.mean_reciprocal_rank == 0.5
    assert summary.abstention_accuracy == 1.0


def test_candidate_recall_is_measured_before_second_stage(case_factory, outcome_factory):
    cases = (case_factory("positive", keys=("right",), mode="advisory"),)
    assert candidate_recall_at_8(cases, {"positive": outcome_factory("wrong", "right")}) == 1.0


def test_local_gate_enforces_memory_and_latency(summary_factory):
    baseline = summary_factory()
    assert passes_tournament_gate(
        baseline, baseline, stable=True,
        operational=OperationalMetrics(peak_rss_mb=499.0, p95_ms=999.0),
        system_kind="local",
    )
    assert not passes_tournament_gate(
        baseline, baseline, stable=True,
        operational=OperationalMetrics(peak_rss_mb=501.0, p95_ms=999.0),
        system_kind="local",
    )


def test_winner_policy_prefers_precision_then_accuracy_then_local_tie(system_factory):
    local = system_factory("minilm", "local", false_positive_rate=0.0, top1=0.8)
    llm = system_factory("llm", "llm", false_positive_rate=0.0, top1=0.8)
    assert select_recommendation((llm, local)).name == "minilm"


def test_metric_summary_new_fields_preserve_existing_positional_construction():
    summary = MetricSummary(1, 1, 1, 1, 0, 0, 0)
    assert summary.reciprocal_rank_sum == 0.0
    assert summary.correct_abstentions == 0


def test_score_floor_rejects_held_out_cases(case_factory, outcome_factory, summary_factory):
    held_out = (case_factory("case", keys=("right",), mode="advisory"),)
    with pytest.raises(ValueError, match="calibration"):
        select_score_floor(held_out, {"case": outcome_factory("right")}, summary_factory())


def test_score_floor_uses_calibration_and_prefers_highest_equal_floor(case_factory, outcome_factory, summary_factory):
    cases = (case_factory("case", keys=("right",), mode="advisory", split="calibration"),)
    outcomes = {"case": outcome_factory("right", "right")}
    assert select_score_floor(cases, outcomes, summary_factory()) == 1.0


def test_recommendation_excludes_bm25_control_and_failed_challengers(system_factory):
    bm25 = system_factory("bm25", "bm25")
    failed = system_factory("failed", "local", passed=False)
    assert select_recommendation((bm25, failed)) is None
