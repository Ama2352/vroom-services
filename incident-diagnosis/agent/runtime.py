"""Composition root: assemble focused modules into one investigation service."""

from __future__ import annotations

import redis

from collector import EvidenceCollector, HttpObservationClient
from config import Settings
from llm import LLMClient
from retrieval.evidence import EvidenceRetrievalService
from services.investigation import InvestigationService
from stores.incidents import IncidentStore
from stores.knowledge import KnowledgeStore


def build_investigation_service(
    settings: Settings, *, redis_client=None, observation_client=None, generate=None,
) -> InvestigationService:
    """Build the production dependency graph while preserving test injection points."""
    client = redis_client or redis.from_url(settings.redis_url, decode_responses=True)
    knowledge_store = KnowledgeStore(client)
    corpus = knowledge_store.corpus()
    retrieval = EvidenceRetrievalService(corpus)
    collector = EvidenceCollector(observation_client or HttpObservationClient(settings))
    generator = generate or LLMClient(settings).generate

    return InvestigationService(
        collector=collector,
        retrieve=retrieval.retrieve,
        find_knowledge=corpus.knowledge,
        generate=generator,
        incident_store=IncidentStore(client),
    )
