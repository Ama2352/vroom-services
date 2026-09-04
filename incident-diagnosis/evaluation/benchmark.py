"""Small adapters that run frozen cases through the clean retrieval contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from evidence import normalize_evidence
from retrieval.evidence import EvidenceRetrievalService
from stores.knowledge import KnowledgeCorpus


_MODES = {"exact", "nearest", "none", "degraded"}
_SPLITS = {"calibration", "held_out"}
_COVERAGE_FIELDS = (
    "service", "triggering_metric", "log_error", "trace_error_service",
    "trace_error_operation", "trace_error_message", "configuration_diff",
)


@dataclass(frozen=True)
class RetrievalCase:
    """One frozen incident expressed in the clean evidence input shape."""

    case_id: str
    split: str
    alert: dict
    facts: dict
    log: dict
    trace: dict
    configuration: dict
    expected_mode: str
    expected_keys: tuple[str, ...]
    forbidden_keys: tuple[str, ...]
    competing_keys: tuple[str, ...] = ()
    shared_keywords: tuple[str, ...] = ()
    decisive_fields: tuple[str, ...] = ()
    provenance: str = ""
    rationale: str = ""


@dataclass(frozen=True)
class EvaluationResult:
    """Counts used for simple quality and safety comparisons."""

    name: str
    exact_correct: int
    exact_total: int
    advisory_top1: int
    advisory_recall_at_3: int
    advisory_mrr_sum: float
    advisory_positive_count: int
    false_positives: int
    forbidden_acceptances: int
    exact_failures: int
    correct_abstentions: int
    degraded_count: int


class IdentityReranker:
    """BM25-only baseline: keep the lexical candidate order unchanged."""

    def rerank(self, _query: str, candidates):
        return tuple(candidates)


def load_cases(path: Path) -> tuple[RetrievalCase, ...]:
    """Load labelled cases without accepting ambiguous fixture records."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("case fixture must be a JSON array")

    cases, seen = [], set()
    for item in raw:
        case_id = item.get("case_id") if isinstance(item, dict) else None
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("case IDs must be unique non-empty strings")
        seen.add(case_id)
        mode, split = item.get("expected_mode"), item.get("split")
        expected = tuple(item.get("expected_keys") or ())
        if mode not in _MODES or split not in _SPLITS:
            raise ValueError(f"{case_id}: invalid split or retrieval mode")
        if mode in {"exact", "nearest"} and not expected:
            raise ValueError(f"{case_id}: positive cases need expected keys")
        if mode == "none" and expected:
            raise ValueError(f"{case_id}: none cases may not have expected keys")
        cases.append(RetrievalCase(
            case_id, split, dict(item.get("alert") or {}), dict(item.get("facts") or {}),
            dict(item.get("log") or {}), dict(item.get("trace") or {}),
            dict(item.get("configuration") or {}), mode, expected,
            tuple(item.get("forbidden_keys") or ()), tuple(item.get("competing_keys") or ()),
            tuple(item.get("shared_keywords") or ()), tuple(item.get("decisive_fields") or ()),
            str(item.get("provenance") or ""), str(item.get("rationale") or ""),
        ))
    return tuple(cases)


def load_snapshot(path: Path) -> dict:
    """Load the same families/examples/hints document used by KnowledgeCorpus."""
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    base_name = snapshot.get("base_snapshot") if isinstance(snapshot, dict) else None
    if base_name:
        if not isinstance(base_name, str) or Path(base_name).name != base_name:
            raise ValueError("base_snapshot must name a sibling fixture")
        base = load_snapshot(path.parent / base_name)
        snapshot = {
            key: [*base[key], *(snapshot.get(key) or [])]
            for key in ("families", "examples", "hints")
        }
    if not isinstance(snapshot, dict) or any(not isinstance(snapshot.get(key), list) for key in ("families", "examples", "hints")):
        raise ValueError("knowledge snapshot needs families, examples, and hints lists")
    return snapshot


def load_model_specs(path: Path) -> dict[str, dict]:
    """Load pinned local-model details needed to reproduce a comparison."""
    specs = json.loads(path.read_text(encoding="utf-8"))
    required = {"name", "repo_id", "revision", "onnx_file", "sha256", "license", "max_length"}
    if not isinstance(specs, dict) or set(specs) != {"minilm", "mixedbread_xsmall"}:
        raise ValueError("model specs must define minilm and mixedbread_xsmall")
    if any(not isinstance(spec, dict) or set(spec) != required for spec in specs.values()):
        raise ValueError("each model spec must have the pinned reranker fields")
    return specs


def build_template(case: RetrievalCase):
    """Use the production normalizer so fixture fingerprints stay meaningful."""
    return normalize_evidence(case.alert, case.facts, case.log, case.trace, case.configuration)


def field_coverage(cases) -> dict[str, float]:
    """Return the fraction of cases with each important evidence field populated."""
    cases = tuple(cases)
    if not cases:
        return {field: 0.0 for field in _COVERAGE_FIELDS}
    populated = {field: 0 for field in _COVERAGE_FIELDS}
    for case in cases:
        values = dict(build_template(case).values)
        for field in populated:
            populated[field] += bool(values.get(field))
    return {field: count / len(cases) for field, count in populated.items()}


def evidence_category_coverage(cases) -> dict[str, int]:
    """Count natural evidence mixes represented by the frozen case set."""
    categories = {
        "kubernetes_heavy": 0,
        "metrics_plus_logs": 0,
        "logs_plus_traces": 0,
        "configuration_related": 0,
        "sparse_no_match": 0,
        "conflicting_evidence": 0,
    }
    for case in cases:
        values = dict(build_template(case).values)
        kubernetes = ("waiting_reason", "last_terminated_reason", "event_reason", "event_message")
        source_groups = (
            sum(bool(values[field]) for field in kubernetes) > 0,
            bool(values["log_error"]),
            any(bool(values[field]) for field in ("trace_error_service", "trace_error_operation", "trace_error_message")),
            bool(values["configuration_diff"]),
        )
        categories["kubernetes_heavy"] += sum(bool(values[field]) for field in kubernetes) >= 2
        categories["metrics_plus_logs"] += bool(values["triggering_metric"] and values["log_error"])
        categories["logs_plus_traces"] += bool(source_groups[1] and source_groups[2])
        categories["configuration_related"] += source_groups[3]
        categories["sparse_no_match"] += case.expected_mode == "none" and sum(source_groups) <= 1
        categories["conflicting_evidence"] += bool(case.competing_keys) and sum(source_groups) >= 2
    return categories


def retrieve_case(case: RetrievalCase, snapshot: dict, reranker):
    """Exercise the clean exact-and-advisory retrieval pipeline for one case."""
    return EvidenceRetrievalService(KnowledgeCorpus(snapshot), reranker).retrieve(build_template(case))


def pipeline_trace(case: RetrievalCase, snapshot: dict, reranker, *, name: str, score_floor: float) -> dict:
    """Expose BM25, reranking, and threshold stages for one audit case."""
    bm25 = retrieve_case(case, snapshot, IdentityReranker())
    reranked = retrieve_case(case, snapshot, reranker)
    accepted = tuple(
        item for item in reranked.candidates
        if reranked.mode.value != "nearest" or _candidate_score(item, name=name) >= score_floor
    )
    describe = lambda items: [
        {"key": item.knowledge_key, "bm25": item.bm25_score, "reranker": item.reranker_score,
         "matched_terms": item.matched_terms}
        for item in items
    ]
    return {"bm25_candidates": describe(bm25.candidates), "raw_reranker_candidates": describe(reranked.candidates),
            "score_floor": score_floor, "accepted_candidates": describe(accepted)}


def run_system(cases, snapshot: dict, reranker, *, name: str, score_floor: float | None = None) -> EvaluationResult:
    """Score one reranker using the production retrieval modes and fixtures."""
    counts = {
        "exact_correct": 0, "exact_total": 0, "advisory_top1": 0,
        "advisory_recall_at_3": 0, "advisory_mrr_sum": 0.0,
        "advisory_positive_count": 0, "false_positives": 0,
        "forbidden_acceptances": 0, "exact_failures": 0,
        "correct_abstentions": 0, "degraded_count": 0,
    }

    for case in cases:
        result = retrieve_case(case, snapshot, reranker)
        candidates = result.candidates
        if score_floor is not None and result.mode.value == "nearest":
            candidates = tuple(
                item for item in candidates
                if _candidate_score(item, name=name) >= score_floor
            )
        candidate_keys = tuple(item.knowledge_key for item in candidates)
        if result.mode.value == "degraded":
            counts["degraded_count"] += 1
        if set(candidate_keys).intersection(case.forbidden_keys):
            counts["forbidden_acceptances"] += 1

        if case.expected_mode == "exact":
            counts["exact_total"] += 1
            if result.mode.value == "exact" and set(candidate_keys).intersection(case.expected_keys):
                counts["exact_correct"] += 1
            else:
                counts["exact_failures"] += 1
        elif case.expected_mode == "nearest":
            counts["advisory_positive_count"] += 1
            expected_ranks = [
                index for index, key in enumerate(candidate_keys, start=1)
                if key in case.expected_keys
            ]
            if expected_ranks:
                first_rank = expected_ranks[0]
                counts["advisory_recall_at_3"] += first_rank <= 3
                counts["advisory_mrr_sum"] += 1 / first_rank
                counts["advisory_top1"] += first_rank == 1
        # A score floor can reject a nearest candidate.  Treat that empty
        # accepted set as an abstention, not as the pre-filtered mode.
        elif not candidate_keys:
            counts["correct_abstentions"] += 1
        else:
            counts["false_positives"] += 1

    return EvaluationResult(name=name, **counts)


def _candidate_score(candidate, *, name: str) -> float:
    """Use the score that actually ordered this system's candidates."""
    return candidate.bm25_score if name == "bm25" else candidate.reranker_score


def calibrate_score_floor(cases, snapshot: dict, reranker, *, name: str) -> float:
    """Choose the lowest calibration floor with no false positives or forbidden keys."""
    calibration = tuple(case for case in cases if case.split == "calibration")
    scores = {0.0}
    for case in calibration:
        result = retrieve_case(case, snapshot, reranker)
        scores.update(_candidate_score(item, name=name) for item in result.candidates)
    passing = []
    for floor in sorted(scores):
        result = run_system(calibration, snapshot, reranker, name=name, score_floor=floor)
        if result.false_positives == 0 and result.forbidden_acceptances == 0 and result.exact_failures == 0:
            passing.append((result.advisory_top1, result.advisory_recall_at_3, -floor, floor))
    return max(passing)[-1] if passing else float("inf")


def passes_gate(result: EvaluationResult, *, baseline: EvaluationResult) -> bool:
    """Reject unsafe systems and require them to match the lexical baseline."""
    return (
        result.forbidden_acceptances == 0
        and result.false_positives == 0
        and result.exact_failures == 0
        and result.degraded_count == 0
        and result.advisory_top1 >= baseline.advisory_top1
        and result.advisory_recall_at_3 >= baseline.advisory_recall_at_3
        and result.advisory_mrr_sum >= baseline.advisory_mrr_sum
    )
