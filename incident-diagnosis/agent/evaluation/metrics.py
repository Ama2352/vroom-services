from dataclasses import replace
from typing import Iterable, Mapping

from evaluation.bm25_variants import rank_bm25
from evaluation.models import (
    MetricSummary,
    RetrievalCase,
    RetrievalOutcome,
    VariantConfig,
)


def summarize(
    cases: Iterable[RetrievalCase],
    outcomes: Mapping[str, RetrievalOutcome],
) -> MetricSummary:
    cases = tuple(cases)
    case_ids = {case.id for case in cases}
    outcome_ids = set(outcomes)
    if case_ids != outcome_ids or len(case_ids) != len(cases):
        raise ValueError(
            f"outcome IDs do not match case IDs: "
            f"missing={sorted(case_ids - outcome_ids)}, "
            f"extra={sorted(outcome_ids - case_ids)}"
        )

    positive_cases = 0
    no_match_cases = 0
    top1_correct = 0
    top3_correct = 0
    false_positives = 0
    forbidden_acceptances = 0
    exact_failures = 0

    for case in cases:
        outcome = outcomes[case.id]
        keys = tuple(candidate.knowledge_key for candidate in outcome.candidates)
        expected = set(case.expected_keys)

        if case.expected_mode in {"exact", "advisory"}:
            positive_cases += 1
            top1_correct += bool(keys and keys[0] in expected)
            top3_correct += bool(expected.intersection(keys[:3]))
        else:
            no_match_cases += 1
            false_positives += bool(keys)

        forbidden_acceptances += bool(set(case.forbidden_keys).intersection(keys))

        if case.expected_mode == "exact":
            exact_failures += not (
                outcome.mode == "exact"
                and keys
                and keys[0] in expected
            )
        elif outcome.mode == "exact":
            exact_failures += 1

    return MetricSummary(
        positive_cases,
        no_match_cases,
        top1_correct,
        top3_correct,
        false_positives,
        forbidden_acceptances,
        exact_failures,
    )


def passes_gate(candidate: MetricSummary, baseline: MetricSummary) -> bool:
    return (
        candidate.top1_accuracy >= baseline.top1_accuracy
        and candidate.recall_at_3 >= baseline.recall_at_3
        and candidate.false_positive_rate <= baseline.false_positive_rate
        and candidate.forbidden_acceptances == 0
        and candidate.exact_failures == 0
    )


def _at_threshold(
    outcomes: Mapping[str, RetrievalOutcome],
    threshold: float,
) -> dict[str, RetrievalOutcome]:
    filtered = {}
    for case_id, outcome in outcomes.items():
        if outcome.mode == "exact":
            filtered[case_id] = outcome
            continue
        candidates = tuple(
            candidate
            for candidate in outcome.candidates
            if candidate.score >= threshold
        )
        filtered[case_id] = RetrievalOutcome(
            mode="advisory" if candidates else "none",
            candidates=candidates,
            exact_ambiguous=outcome.exact_ambiguous,
        )
    return filtered


def select_threshold(
    cases: Iterable[RetrievalCase],
    unthresholded_outcomes: Mapping[str, RetrievalOutcome],
    baseline_metrics: MetricSummary,
) -> float | None:
    cases = tuple(cases)
    floors = {0.0}
    floors.update(
        candidate.score
        for outcome in unthresholded_outcomes.values()
        if outcome.mode != "exact"
        for candidate in outcome.candidates
        if candidate.score > 0
    )
    passing = []
    for floor in sorted(floors):
        metrics = summarize(
            cases, _at_threshold(unthresholded_outcomes, floor)
        )
        if passes_gate(metrics, baseline_metrics):
            passing.append((
                metrics.false_positive_rate,
                -metrics.top1_accuracy,
                -metrics.recall_at_3,
                -floor,
                floor,
            ))
    return min(passing)[-1] if passing else None


def calibrate_variant(
    rdb,
    calibration_cases: Iterable[RetrievalCase],
    config: VariantConfig,
    baseline_metrics: MetricSummary,
) -> VariantConfig | None:
    calibration_cases = tuple(calibration_cases)
    if any(case.split != "calibration" for case in calibration_cases):
        raise ValueError("calibrate_variant accepts calibration cases only")
    unthresholded = replace(config, threshold=0.0)
    outcomes = {
        case.id: rank_bm25(rdb, case, unthresholded)
        for case in calibration_cases
    }
    selected = select_threshold(
        calibration_cases, outcomes, baseline_metrics
    )
    return replace(config, threshold=selected) if selected is not None else None
