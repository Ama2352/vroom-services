from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .bm25 import BM25Index
from .models import RetrievalDocument


# These are template/domain glue words, never decisive observations.
_NON_DECISIVE_TERMS = {"service", "namespace", "alert", "metric", "configuration"}


class EvidenceRetrievalMode(str, Enum):
    EXACT = "exact"
    NEAREST = "nearest"
    NONE = "none"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class EvidenceCandidate:
    knowledge_key: str
    example_id: str
    serialized: str
    bm25_score: float = 0.0
    reranker_score: float = 0.0


@dataclass(frozen=True)
class EvidenceRetrievalResult:
    mode: EvidenceRetrievalMode
    candidates: tuple[EvidenceCandidate, ...] = ()
    exact_ambiguous: bool = False
    degraded_reason: str | None = None


def _serialized(document: dict) -> str:
    evidence = document.get("evidence_text", "")
    hints = " ".join(document.get("hint_texts") or ())
    return f"{evidence}\napproved_hints: {hints}".strip()


def _retrieval_text(serialized: str) -> str:
    """Compare observed values, not the shared fixed-template field names."""
    values = []
    for line in serialized.splitlines():
        if ":" not in line:
            continue
        _, value = line.split(":", 1)
        value = value.strip()
        if value:
            values.append(value)
    return "\n".join(values)


class EvidenceRetrievalService:
    def __init__(self, corpus, reranker):
        self.corpus = corpus
        self.reranker = reranker

    def retrieve(self, template) -> EvidenceRetrievalResult:
        try:
            documents = tuple(self.corpus.get_documents())
            exact = [
                item for item in documents
                if item.get("exact_reusable") and item.get("fingerprint") == template.fingerprint()
            ]
            keys = {item.get("knowledge_key") for item in exact}
            if len(keys) == 1:
                item = exact[0]
                return EvidenceRetrievalResult(
                    EvidenceRetrievalMode.EXACT,
                    (EvidenceCandidate(item["knowledge_key"], item["example_id"], _serialized(item)),),
                )

            if not documents:
                return EvidenceRetrievalResult(EvidenceRetrievalMode.NONE, exact_ambiguous=len(keys) > 1)
            retrieval_docs = tuple(_as_document(item) for item in documents)
            index = BM25Index(retrieval_docs)
            ranked = index.search(_retrieval_text(template.serialize()), limit=8)
            if not ranked:
                return EvidenceRetrievalResult(EvidenceRetrievalMode.NONE, exact_ambiguous=len(keys) > 1)
            by_id = {item["example_id"]: item for item in documents}
            candidates = []
            for ranked_item in ranked:
                raw = by_id[ranked_item.document.source_id]
                if not set(ranked_item.matched_terms).difference(_NON_DECISIVE_TERMS):
                    continue
                candidates.append(EvidenceCandidate(
                    raw["knowledge_key"], raw["example_id"], _serialized(raw), ranked_item.bm25_score,
                ))
            reranked = self.reranker.rerank(template.serialize(), tuple(candidates))
            families = []
            seen = set()
            for candidate in reranked:
                if candidate.knowledge_key in seen:
                    continue
                seen.add(candidate.knowledge_key)
                families.append(candidate)
                if len(families) == 3:
                    break
            return EvidenceRetrievalResult(
                EvidenceRetrievalMode.NEAREST,
                tuple(families),
                exact_ambiguous=len(keys) > 1,
            )
        except Exception as exc:
            return EvidenceRetrievalResult(EvidenceRetrievalMode.DEGRADED, degraded_reason=type(exc).__name__)


def _as_document(item: dict) -> RetrievalDocument:
    return RetrievalDocument(
        knowledge_key=item["knowledge_key"],
        source="example",
        source_id=item["example_id"],
        trigger="",
        conclusive=False,
        root_cause_pattern="",
        fix_action="",
        document_text=_retrieval_text(_serialized(item)),
    )
