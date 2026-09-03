"""Composition root: assemble focused modules into one investigation service."""

from __future__ import annotations

import os
from pathlib import Path

import redis

from collector import EvidenceCollector, HttpObservationClient
from config import Settings
from llm import LLMClient
from retrieval.evidence import EvidenceRetrievalService
from retrieval.reranker import MiniLMReranker, OnnxCrossEncoder, load_model_spec
from services.investigation import InvestigationService
from stores.incidents import IncidentStore
from stores.knowledge import KnowledgeStore


class UnavailableReranker:
    """Make missing local model artifacts a safe degraded retrieval result."""

    def rerank(self, query, candidates):
        raise RuntimeError("MiniLM reranker is not configured")


def build_investigation_service(
    settings: Settings, *, redis_client=None, observation_client=None, reranker=None, generate=None,
) -> InvestigationService:
    """Build the production dependency graph while preserving test injection points."""
    client = redis_client or redis.from_url(settings.redis_url, decode_responses=True)
    knowledge_store = KnowledgeStore(client)
    corpus = knowledge_store.corpus()
    retrieval = EvidenceRetrievalService(corpus, reranker or _configured_reranker())
    collector = EvidenceCollector(observation_client or HttpObservationClient(settings))
    generator = generate or LLMClient(settings).generate

    return InvestigationService(
        collector=collector,
        retrieve=retrieval.retrieve,
        find_knowledge=corpus.knowledge,
        generate=generator,
        incident_store=IncidentStore(client),
    )


def _configured_reranker():
    """Load only a locally verified model; missing artifacts remain a safe degraded path."""
    model_dir = os.environ.get("RERANKER_MODEL_DIR", "")
    if not model_dir:
        return UnavailableReranker()
    try:
        manifest = Path(__file__).parent / "retrieval" / "model_manifest.json"
        return MiniLMReranker(OnnxCrossEncoder(Path(model_dir), load_model_spec(manifest)))
    except Exception:
        return UnavailableReranker()
