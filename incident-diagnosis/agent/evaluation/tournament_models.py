from dataclasses import dataclass, field
from typing import Literal

from evaluation.models import MetricSummary, RetrievalOutcome


SystemKind = Literal["baseline", "bm25", "local", "llm"]


@dataclass(frozen=True)
class CandidateDecision:
    knowledge_key: str
    accepted: bool
    score: float | None
    grade: int | None = None
    supporting_fields: tuple[str, ...] = ()
    conflicting_fields: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class DecisionTrace:
    outcome: RetrievalOutcome
    decisions: tuple[CandidateDecision, ...] = ()
    latency_ms: float = 0.0
    error: str | None = None


@dataclass(frozen=True)
class OperationalMetrics:
    artifact_mb: float | None = None
    estimated_container_delta_mb: float | None = None
    cold_load_ms: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    peak_rss_mb: float | None = None
    request_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    current_spend_usd: float = 0.0
    theoretical_spend_usd: float | None = None


@dataclass(frozen=True)
class SystemEvaluation:
    name: str
    kind: SystemKind
    calibration: MetricSummary
    held_out: MetricSummary
    threshold: float | None
    stable: bool
    passed: bool
    operational: OperationalMetrics = field(default_factory=OperationalMetrics)
    failure_reasons: tuple[str, ...] = ()
