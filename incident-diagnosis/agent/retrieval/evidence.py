"""Exact and advisory retrieval over approved incident examples."""

from __future__ import annotations

from .bm25 import BM25Index, evidence_values, tokenize
from .models import EvidenceCandidate, EvidenceRetrievalMode, EvidenceRetrievalResult


# Identity fields help BM25 find a relevant neighbourhood, but cannot by
# themselves justify asking the semantic reranker for advisory guidance.
_IDENTITY_FIELDS = {
    "alert_name",
    "service",
    "triggering_metric",
    "trace_error_service",
}
# These connect ordinary sentences but do not identify an incident family.
_NON_DIAGNOSTIC_TERMS = {"and", "failed"}


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


def _symptom_terms(template) -> set[str]:
    """Return query terms from observations that distinguish one incident."""
    observation_text = "\n".join(
        value for field, value in template.values
        if field not in _IDENTITY_FIELDS and value
    )
    identity_text = "\n".join(
        value for field, value in template.values
        if field in _IDENTITY_FIELDS and value
    )
    # A service name can be repeated in an error message without becoming a symptom.
    return set(tokenize(observation_text)).difference(
        tokenize(identity_text), _NON_DIAGNOSTIC_TERMS,
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
            symptom_terms = _symptom_terms(template)
            decisive = tuple(
                item for item in ranked
                if set(item.matched_terms).intersection(symptom_terms)
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
