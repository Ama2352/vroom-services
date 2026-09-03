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
            tuple(item.get("forbidden_keys") or ()),
        ))
    return tuple(cases)


def load_snapshot(path: Path) -> dict:
    """Load the same families/examples/hints document used by KnowledgeCorpus."""
    snapshot = json.loads(path.read_text(encoding="utf-8"))
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


def retrieve_case(case: RetrievalCase, snapshot: dict, reranker):
    """Exercise the clean exact-and-advisory retrieval pipeline for one case."""
    return EvidenceRetrievalService(KnowledgeCorpus(snapshot), reranker).retrieve(build_template(case))


def run_system(cases, snapshot: dict, reranker, *, name: str) -> EvaluationResult:
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
        candidate_keys = tuple(item.knowledge_key for item in result.candidates)
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
        elif result.mode.value == "none":
            counts["correct_abstentions"] += 1
        else:
            counts["false_positives"] += 1

    return EvaluationResult(name=name, **counts)


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
