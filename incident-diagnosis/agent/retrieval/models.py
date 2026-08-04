from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


ACCEPTANCE_FLOOR = 1.1961885690689087


class RetrievalMode(str, Enum):
    EXACT_CONCLUSIVE = "exact_conclusive"
    RERANKED_ADVISORY = "reranked_advisory"
    NONE = "none"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class RetrievalDocument:
    knowledge_key: str
    source: str
    source_id: str
    trigger: str
    conclusive: bool
    root_cause_pattern: str
    fix_action: str
    document_text: str
    context_notes: str = ""


@dataclass(frozen=True)
class RankedCandidate:
    document: RetrievalDocument
    bm25_score: float
    matched_terms: tuple[str, ...] = ()

    @property
    def knowledge_key(self) -> str:
        return self.document.knowledge_key


@dataclass(frozen=True)
class RetrievalResult:
    mode: RetrievalMode
    accepted: bool
    candidate: RankedCandidate | None = None
    reranker_score: float | None = None
    corpus_version: int | None = None
    stale_snapshot: bool = False
    exact_ambiguous: bool = False
    degraded_reason: str | None = None
    ranking: tuple[tuple[str, float, float], ...] = ()

    @classmethod
    def exact_conclusive(
        cls, document: RetrievalDocument, corpus_version: int,
        *, stale_snapshot: bool = False,
    ) -> "RetrievalResult":
        return cls(
            RetrievalMode.EXACT_CONCLUSIVE,
            True,
            candidate=RankedCandidate(document, 0.0),
            corpus_version=corpus_version,
            stale_snapshot=stale_snapshot,
        )

    @classmethod
    def accepted_advisory(
        cls, candidate: RankedCandidate, reranker_score: float,
        corpus_version: int, *, stale_snapshot: bool = False,
        exact_ambiguous: bool = False,
        ranking: tuple[tuple[str, float, float], ...] = (),
    ) -> "RetrievalResult":
        return cls(
            RetrievalMode.RERANKED_ADVISORY,
            True,
            candidate=candidate,
            reranker_score=reranker_score,
            corpus_version=corpus_version,
            stale_snapshot=stale_snapshot,
            exact_ambiguous=exact_ambiguous,
            ranking=ranking,
        )

    @classmethod
    def none(
        cls, corpus_version: int | None = None, *, stale_snapshot: bool = False,
        exact_ambiguous: bool = False,
        ranking: tuple[tuple[str, float, float], ...] = (),
    ) -> "RetrievalResult":
        return cls(
            RetrievalMode.NONE,
            False,
            corpus_version=corpus_version,
            stale_snapshot=stale_snapshot,
            exact_ambiguous=exact_ambiguous,
            ranking=ranking,
        )

    @classmethod
    def degraded(
        cls, reason: str, corpus_version: int | None = None,
        *, stale_snapshot: bool = False,
    ) -> "RetrievalResult":
        return cls(
            RetrievalMode.DEGRADED,
            False,
            corpus_version=corpus_version,
            stale_snapshot=stale_snapshot,
            degraded_reason=reason,
        )

    def to_api_dict(self, debug: bool = False) -> dict[str, Any]:
        source = None
        if self.candidate:
            source = (
                "knowledge_with_history"
                if self.candidate.document.source == "history"
                else "knowledge"
            )
        payload: dict[str, Any] = {
            "mode": self.mode.value,
            "accepted": self.accepted,
            "source": source,
        }
        if self.candidate:
            payload["knowledge_key"] = self.candidate.knowledge_key
        if debug:
            payload["debug"] = {
                "corpus_version": self.corpus_version,
                "stale_snapshot": self.stale_snapshot,
                "acceptance_floor": ACCEPTANCE_FLOOR,
                "exact_ambiguous": self.exact_ambiguous,
                "degraded_reason": self.degraded_reason,
                "ranking": [
                    {
                        "knowledge_key": key,
                        "bm25_score": bm25,
                        "reranker_score": rerank,
                    }
                    for key, bm25, rerank in self.ranking
                ],
            }
        return payload
