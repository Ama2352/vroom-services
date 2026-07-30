from evaluation.metrics import passes_gate, select_threshold, summarize
from evaluation.models import (
    MetricSummary,
    RankedCandidate,
    RetrievalCase,
    RetrievalOutcome,
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
