"""Coordinate one evidence-first incident investigation."""

from __future__ import annotations

from evidence import normalize_collected_evidence
from investigation import decide_diagnosis
from presentation import build_incident_response
from retrieval.models import EvidenceRetrievalMode, EvidenceRetrievalResult


class InvestigationService:
    """Join collection, retrieval, diagnosis, and persistence without owning them."""

    def __init__(self, *, collector, retrieve, find_knowledge, generate, incident_store):
        self._collector = collector
        self._retrieve = retrieve
        self._find_knowledge = find_knowledge
        self._generate = generate
        self._incident_store = incident_store

    def investigate(self, alert: dict) -> dict:
        """Produce one stored response from an alert's current service scope."""
        collected = self._collector.collect(alert)
        template = normalize_collected_evidence(alert, collected.raw)
        retrieval = self._retrieve(template)
        knowledge = self._exact_knowledge(retrieval)
        diagnosis = decide_diagnosis(template, retrieval, self._generate, knowledge=knowledge)
        response = build_incident_response(
            template,
            collected.raw,
            diagnosis,
            _retrieval_response(retrieval),
        )
        response["missing_evidence"] = list(collected.missing)

        # Save the full decision trace before returning it to any caller.
        return self._incident_store.save(response)

    @property
    def incident_store(self):
        return self._incident_store

    def _exact_knowledge(self, retrieval):
        if retrieval.mode is not EvidenceRetrievalMode.EXACT or not retrieval.candidates:
            return None
        return self._find_knowledge(retrieval.candidates[0].knowledge_key)


def _retrieval_response(result: EvidenceRetrievalResult) -> dict:
    """Expose retrieval provenance as JSON without leaking Python enum objects."""
    return {
        "mode": result.mode.value,
        "examples": [
            {
                "knowledge_key": item.knowledge_key,
                "example_id": item.example_id,
                "bm25_score": item.bm25_score,
                "reranker_score": item.reranker_score,
            }
            for item in result.candidates
        ],
        "exact_ambiguous": result.exact_ambiguous,
        "degraded_reason": result.degraded_reason,
    }
