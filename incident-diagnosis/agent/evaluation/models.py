from dataclasses import dataclass
from typing import Literal


Split = Literal["calibration", "held_out"]
ExpectedMode = Literal["exact", "advisory", "none"]
SourceType = Literal["knowledge", "history"]
QueryVariant = Literal["baseline", "rich"]
HistoryVariant = Literal["plain", "joined"]


@dataclass(frozen=True)
class RetrievalCase:
    id: str
    split: Split
    alert_name: str
    facts: dict
    expected_keys: tuple[str, ...]
    expected_mode: ExpectedMode
    forbidden_keys: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class RankedCandidate:
    knowledge_key: str
    score: float
    source: SourceType
    source_id: str
    matched_terms: tuple[str, ...]
    root_cause_pattern: str
    fix_action: str
    context_notes: str = ""


@dataclass(frozen=True)
class RetrievalOutcome:
    mode: ExpectedMode
    candidates: tuple[RankedCandidate, ...]
    exact_ambiguous: bool = False


@dataclass(frozen=True)
class MetricSummary:
    positive_cases: int
    no_match_cases: int
    top1_correct: int
    top3_correct: int
    false_positives: int
    forbidden_acceptances: int
    exact_failures: int

    @property
    def top1_accuracy(self) -> float:
        return self.top1_correct / self.positive_cases if self.positive_cases else 1.0

    @property
    def recall_at_3(self) -> float:
        return self.top3_correct / self.positive_cases if self.positive_cases else 1.0

    @property
    def false_positive_rate(self) -> float:
        return self.false_positives / self.no_match_cases if self.no_match_cases else 0.0


@dataclass(frozen=True)
class VariantConfig:
    name: str
    query_variant: QueryVariant
    history_variant: HistoryVariant
    threshold: float = 0.0
