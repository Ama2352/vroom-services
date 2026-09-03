"""Exact and advisory retrieval over approved incident examples."""

from __future__ import annotations

from .bm25 import BM25Index, evidence_values
from .models import EvidenceCandidate, EvidenceRetrievalMode, EvidenceRetrievalResult


_NON_DECISIVE_TERMS = {"service", "namespace", "alert", "metric", "configuration"}


def _serialized(document: dict) -> str:
    evidence = document.get("evidence_text", "")
    hints = " ".join(document.get("hint_texts") or ())
    return f"{evidence}\napproved_hints: {hints}".strip()


def _candidate(document: dict) -> EvidenceCandidate:
    return EvidenceCandidate(
        knowledge_key=document["knowledge_key"],
        example_id=document["example_id"],
        serialized=_serialized(document),
    )


class EvidenceRetrievalService:
    """Recognizes evidence; it never turns a similar example into a live cause."""

    def __init__(self, corpus, reranker):
        self.corpus = corpus
        self.reranker = reranker

    def retrieve(self, template) -> EvidenceRetrievalResult:
        try:
            documents = tuple(self.corpus.get_documents())
            # Exact reuse requires one identical, approved example.
            exact = [
                item for item in documents
                if item.get("exact_reusable") and item.get("fingerprint") == template.fingerprint()
            ]
            knowledge_keys = {item.get("knowledge_key") for item in exact}
            if len(knowledge_keys) == 1:
                return EvidenceRetrievalResult(
                    EvidenceRetrievalMode.EXACT, (_candidate(exact[0]),),
                )
            if not documents:
                return EvidenceRetrievalResult(EvidenceRetrievalMode.NONE, exact_ambiguous=len(knowledge_keys) > 1)

            # Similar incidents get advisory guidance, never automatic confirmation.
            ranked = BM25Index(tuple(_candidate(item) for item in documents)).search(
                evidence_values(template.serialize()), limit=8,
            )
            decisive = tuple(
                item for item in ranked
                if set(item.matched_terms).difference(_NON_DECISIVE_TERMS)
            )
            if not decisive:
                return EvidenceRetrievalResult(EvidenceRetrievalMode.NONE, exact_ambiguous=len(knowledge_keys) > 1)

            # MiniLM reranks only the small BM25 set; it does not search new examples.
            reranked = self.reranker.rerank(template.serialize(), decisive)
            families, seen = [], set()
            for candidate in reranked:
                if candidate.knowledge_key not in seen:
                    seen.add(candidate.knowledge_key)
                    families.append(candidate)
                if len(families) == 3:
                    break
            return EvidenceRetrievalResult(
                EvidenceRetrievalMode.NEAREST,
                tuple(families),
                exact_ambiguous=len(knowledge_keys) > 1,
            )
        except Exception as exc:
            return EvidenceRetrievalResult(EvidenceRetrievalMode.DEGRADED, degraded_reason=type(exc).__name__)
