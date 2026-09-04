"""Small data shapes shared by retrieval and reranking."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EvidenceRetrievalMode(str, Enum):
    EXACT = "exact"
    NEAREST = "nearest"
    NONE = "none"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class EvidenceCandidate:
    """One approved example represented only by observable evidence and hints."""

    knowledge_key: str                  # which failure family
    example_id: str                     # which approved past example
    serialized: str                     # its evidence as text
    bm25_score: float = 0.0             # keyword similarty
    reranker_score: float = 0.0         # reserved for offline evaluation adapters
    matched_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceRetrievalResult:
    mode: EvidenceRetrievalMode
    candidates: tuple[EvidenceCandidate, ...] = ()
    exact_ambiguous: bool = False
    degraded_reason: str | None = None
