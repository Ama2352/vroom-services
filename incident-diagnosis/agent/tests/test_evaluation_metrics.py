from dataclasses import replace
from unittest.mock import patch

import pytest

from evaluation.metrics import (
    calibrate_variant,
    passes_gate,
    select_threshold,
    summarize,
)
from evaluation.models import (
    MetricSummary,
    RankedCandidate,
    RetrievalCase,
    RetrievalOutcome,
    VariantConfig,
)


def _candidate(key, score=1.0):
    return RankedCandidate(key, score, "knowledge", key, (), "cause", "fix")


def _case(case_id, keys=(), mode="none", forbidden=()):
    return RetrievalCase(
        case_id, "held_out", "Alert", {}, tuple(keys), mode,
        tuple(forbidden), "test",
    )


def test_summary_counts_correct_top1_top3_and_false_positive():
    cases = (
        _case("positive", ("correct",), "advisory"),
        _case("negative", (), "none"),
    )
    outcomes = {
        "positive": RetrievalOutcome(
            "advisory", (_candidate("wrong"), _candidate("correct", 0.8))
        ),
        "negative": RetrievalOutcome("advisory", (_candidate("wrong"),)),
    }
    summary = summarize(cases, outcomes)
    assert summary.top1_correct == 0
    assert summary.top3_correct == 1
    assert summary.false_positives == 1


def test_summary_counts_forbidden_acceptance():
    cases = (_case("hard", ("correct",), "advisory", ("forbidden",)),)
    outcomes = {
        "hard": RetrievalOutcome("advisory", (_candidate("forbidden"),))
    }
    assert summarize(cases, outcomes).forbidden_acceptances == 1


def test_gate_requires_non_regression_and_no_forbidden_keys():
    baseline = MetricSummary(8, 2, 6, 7, 1, 0, 0)
    passing = MetricSummary(8, 2, 6, 7, 1, 0, 0)
    worse_precision = MetricSummary(8, 2, 5, 7, 1, 0, 0)
    forbidden = MetricSummary(8, 2, 7, 8, 0, 1, 0)
    assert passes_gate(passing, baseline) is True
    assert passes_gate(worse_precision, baseline) is False
    assert passes_gate(forbidden, baseline) is False


def test_exact_failure_always_fails_gate():
    baseline = MetricSummary(8, 2, 6, 7, 1, 0, 0)
    candidate = MetricSummary(8, 2, 8, 8, 0, 0, 1)
    assert passes_gate(candidate, baseline) is False


def test_unexpected_exact_mode_counts_as_exact_failure():
    cases = (_case("ambiguous", ("correct",), "advisory"),)
    outcomes = {
        "ambiguous": RetrievalOutcome("exact", (_candidate("correct"),))
    }
    assert summarize(cases, outcomes).exact_failures == 1


def test_select_threshold_removes_false_positive_without_losing_recall():
    cases = (
        _case("positive", ("correct",), "advisory"),
        _case("negative", (), "none"),
    )
    outcomes = {
        "positive": RetrievalOutcome("advisory", (_candidate("correct", 0.8),)),
        "negative": RetrievalOutcome("advisory", (_candidate("wrong", 0.4),)),
    }
    baseline = MetricSummary(1, 1, 1, 1, 0, 0, 0)
    assert select_threshold(cases, outcomes, baseline) == 0.8


def test_select_threshold_preserves_exact_outcomes():
    cases = (
        _case("exact", ("correct",), "exact"),
        _case("positive", ("correct",), "advisory"),
        _case("negative", (), "none"),
    )
    outcomes = {
        "exact": RetrievalOutcome("exact", (_candidate("correct", 1.0),)),
        "positive": RetrievalOutcome("advisory", (_candidate("correct", 0.8),)),
        "negative": RetrievalOutcome("advisory", (_candidate("wrong", 0.4),)),
    }
    baseline = MetricSummary(2, 1, 2, 2, 0, 0, 0)
    assert select_threshold(cases, outcomes, baseline) == 0.8


def test_select_threshold_returns_none_when_no_floor_passes():
    cases = (_case("positive", ("correct",), "advisory"),)
    outcomes = {
        "positive": RetrievalOutcome("advisory", (_candidate("wrong", 0.8),)),
    }
    baseline = MetricSummary(1, 0, 1, 1, 0, 0, 0)
    assert select_threshold(cases, outcomes, baseline) is None


def test_select_threshold_uses_highest_floor_when_metrics_tie():
    cases = (_case("positive", ("correct",), "advisory"),)
    outcomes = {
        "positive": RetrievalOutcome(
            "advisory",
            (_candidate("correct", 0.8), _candidate("correct", 0.5)),
        ),
    }
    baseline = MetricSummary(1, 0, 1, 1, 0, 0, 0)
    assert select_threshold(cases, outcomes, baseline) == 0.8


def test_calibrate_variant_rejects_held_out_before_ranking():
    config = VariantConfig("candidate", "rich", "joined", threshold=0.6)
    baseline = MetricSummary(0, 1, 0, 0, 0, 0, 0)
    with patch("evaluation.metrics.rank_bm25") as rank:
        with pytest.raises(ValueError, match="calibration"):
            calibrate_variant(object(), (_case("held-out"),), config, baseline)
    rank.assert_not_called()


def test_calibrate_variant_ranks_each_case_once_at_zero_and_replaces_floor():
    cases = (
        replace(
            _case("positive", ("correct",), "advisory"),
            split="calibration",
        ),
        replace(_case("negative"), split="calibration"),
    )
    outcomes = {
        "positive": RetrievalOutcome("advisory", (_candidate("correct", 0.8),)),
        "negative": RetrievalOutcome("advisory", (_candidate("wrong", 0.4),)),
    }
    config = VariantConfig("candidate", "rich", "joined", threshold=0.6)
    baseline = MetricSummary(1, 1, 1, 1, 0, 0, 0)
    rdb = object()

    with patch(
        "evaluation.metrics.rank_bm25",
        side_effect=lambda _rdb, case, _config: outcomes[case.id],
    ) as rank:
        calibrated = calibrate_variant(rdb, cases, config, baseline)

    assert calibrated == replace(config, threshold=0.8)
    assert rank.call_count == len(cases)
    for call, case in zip(rank.call_args_list, cases):
        assert call.args == (rdb, case, replace(config, threshold=0.0))


def test_calibrate_variant_returns_none_when_no_floor_passes():
    case = replace(
        _case("positive", ("correct",), "advisory"),
        split="calibration",
    )
    config = VariantConfig("candidate", "rich", "joined", threshold=0.6)
    baseline = MetricSummary(1, 0, 1, 1, 0, 0, 0)
    with patch(
        "evaluation.metrics.rank_bm25",
        return_value=RetrievalOutcome(
            "advisory", (_candidate("wrong", 0.8),)
        ),
    ):
        assert calibrate_variant(object(), (case,), config, baseline) is None


def test_summary_rejects_missing_or_extra_outcome_ids():
    case = _case("expected")
    try:
        summarize((case,), {})
    except ValueError:
        pass
    else:
        raise AssertionError("missing outcome ID was accepted")

    try:
        summarize((case,), {
            "expected": RetrievalOutcome("none", ()),
            "extra": RetrievalOutcome("none", ()),
        })
    except ValueError:
        pass
    else:
        raise AssertionError("extra outcome ID was accepted")
