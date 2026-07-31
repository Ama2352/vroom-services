from typing import Iterable, Mapping

from evaluation.models import MetricSummary, RetrievalCase, RetrievalOutcome
from evaluation.tournament_models import OperationalMetrics, SystemEvaluation, SystemKind


def _validate_outcomes(cases: tuple[RetrievalCase, ...], outcomes: Mapping[str, RetrievalOutcome]) -> None:
    case_ids = {case.id for case in cases}
    outcome_ids = set(outcomes)
    if case_ids != outcome_ids or len(case_ids) != len(cases):
        raise ValueError(
            f"outcome IDs do not match case IDs: "
            f"missing={sorted(case_ids - outcome_ids)}, "
            f"extra={sorted(outcome_ids - case_ids)}"
        )


def summarize_tournament(
    cases: Iterable[RetrievalCase], outcomes: Mapping[str, RetrievalOutcome]
) -> MetricSummary:
    cases = tuple(cases)
    _validate_outcomes(cases, outcomes)
    positive_cases = no_match_cases = top1_correct = top3_correct = 0
    false_positives = forbidden_acceptances = exact_failures = 0
    reciprocal_rank_sum = 0.0
    correct_abstentions = 0

    for case in cases:
        outcome = outcomes[case.id]
        keys = tuple(candidate.knowledge_key for candidate in outcome.candidates)
        expected = set(case.expected_keys)
        if case.expected_mode in {"exact", "advisory"}:
            positive_cases += 1
            top1_correct += bool(keys and keys[0] in expected)
            top3_correct += bool(expected.intersection(keys[:3]))
            reciprocal_rank_sum += next(
                (1.0 / rank for rank, key in enumerate(keys, start=1) if key in expected), 0.0
            )
        else:
            no_match_cases += 1
            false_positives += bool(keys)
            correct_abstentions += not keys
        forbidden_acceptances += bool(set(case.forbidden_keys).intersection(keys))
        if case.expected_mode == "exact":
            exact_failures += not (outcome.mode == "exact" and keys and keys[0] in expected)
        elif outcome.mode == "exact":
            exact_failures += 1

    return MetricSummary(
        positive_cases, no_match_cases, top1_correct, top3_correct, false_positives,
        forbidden_acceptances, exact_failures, reciprocal_rank_sum, correct_abstentions,
    )


def candidate_recall_at_8(
    cases: Iterable[RetrievalCase], outcomes: Mapping[str, RetrievalOutcome]
) -> float:
    cases = tuple(cases)
    _validate_outcomes(cases, outcomes)
    positive_cases = 0
    recalled = 0
    for case in cases:
        if case.expected_mode in {"exact", "advisory"}:
            positive_cases += 1
            expected = set(case.expected_keys)
            recalled += bool(expected.intersection(
                candidate.knowledge_key for candidate in outcomes[case.id].candidates[:8]
            ))
    return recalled / positive_cases if positive_cases else 1.0


def _with_score_floor(
    outcomes: Mapping[str, RetrievalOutcome], floor: float
) -> dict[str, RetrievalOutcome]:
    filtered = {}
    for case_id, outcome in outcomes.items():
        if outcome.mode == "exact":
            filtered[case_id] = outcome
            continue
        candidates = tuple(candidate for candidate in outcome.candidates if candidate.score >= floor)
        filtered[case_id] = RetrievalOutcome(
            "advisory" if candidates else "none", candidates, outcome.exact_ambiguous
        )
    return filtered


def select_score_floor(
    calibration_cases: Iterable[RetrievalCase],
    unthresholded_outcomes: Mapping[str, RetrievalOutcome],
    baseline_metrics: MetricSummary,
) -> float | None:
    cases = tuple(calibration_cases)
    if any(case.split != "calibration" for case in cases):
        raise ValueError("select_score_floor accepts calibration cases only")
    _validate_outcomes(cases, unthresholded_outcomes)
    floors = {0.0}
    floors.update(
        candidate.score
        for outcome in unthresholded_outcomes.values() if outcome.mode != "exact"
        for candidate in outcome.candidates if candidate.score > 0
    )
    passing = []
    for floor in sorted(floors):
        metrics = summarize_tournament(cases, _with_score_floor(unthresholded_outcomes, floor))
        if passes_tournament_gate(
            metrics, baseline_metrics, stable=True, operational=OperationalMetrics(), system_kind="bm25"
        ):
            passing.append((
                metrics.forbidden_acceptances, metrics.exact_failures,
                metrics.false_positive_rate, -metrics.top1_accuracy,
                -metrics.recall_at_3, -floor, floor,
            ))
    return min(passing)[-1] if passing else None


def passes_tournament_gate(
    candidate: MetricSummary, baseline: MetricSummary, *, stable: bool,
    operational: OperationalMetrics, system_kind: SystemKind,
) -> bool:
    quality = (
        candidate.forbidden_acceptances == 0
        and candidate.exact_failures == 0
        and candidate.false_positive_rate <= baseline.false_positive_rate
        and candidate.top1_accuracy >= baseline.top1_accuracy
        and candidate.recall_at_3 >= baseline.recall_at_3
        and stable
    )
    if system_kind == "local":
        return quality and (
            operational.peak_rss_mb is not None and operational.peak_rss_mb <= 500.0
            and operational.p95_ms is not None and operational.p95_ms <= 1000.0
        )
    return quality


def select_recommendation(systems: Iterable[SystemEvaluation]) -> SystemEvaluation | None:
    candidates = tuple(
        system for system in systems if system.passed and system.kind in {"local", "llm"}
    )
    if not candidates:
        return None
    return min(candidates, key=lambda system: (
        system.held_out.false_positive_rate,
        -system.held_out.top1_accuracy,
        -system.held_out.recall_at_3,
        system.operational.current_spend_usd,
        float("inf") if system.operational.p95_ms is None else system.operational.p95_ms,
        float("inf") if system.operational.peak_rss_mb is None else system.operational.peak_rss_mb,
        0 if system.kind == "local" else 1,
        system.name,
    ))
