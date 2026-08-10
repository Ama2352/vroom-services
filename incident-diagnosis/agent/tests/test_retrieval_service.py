from dataclasses import replace
from unittest.mock import Mock

from evidence_projection import build_evidence_projection
from retrieval.corpus import ApprovedCorpusSnapshot, CorpusUnavailable
from retrieval.models import RankedCandidate, RetrievalDocument, RetrievalMode
from retrieval.reranker import ScoredCandidate
from retrieval.service import RetrievalService


class StaticBM25:
    def __init__(self, candidates=()): self.candidates = tuple(candidates)
    def search(self, query, limit=8): return self.candidates[:limit]


class StaticProvider:
    def __init__(self, snapshot): self.snapshot = snapshot
    def get_snapshot(self): return self.snapshot, False


def candidate(key: str) -> RankedCandidate:
    document = RetrievalDocument(key, "knowledge", key, "", False, f"{key} cause", f"fix {key}", f"{key} evidence")
    return RankedCandidate(document, 1.0)


def service(snapshot, reranker):
    return RetrievalService(StaticProvider(snapshot), reranker)


def test_projection_is_the_only_retrieval_input():
    item = candidate("contract")
    bm25 = Mock(search=Mock(return_value=(item,)))
    reranker = Mock(rerank=Mock(return_value=(ScoredCandidate(item, 2.0),)))
    snapshot = ApprovedCorpusSnapshot(9, (), {}, bm25)
    projection = build_evidence_projection("DLQEventsDetected", {"log_error": "unsupported event type Trip.Requested.v2"})

    result = service(snapshot, reranker).retrieve(projection)

    assert result.mode is RetrievalMode.RERANKED_ADVISORY
    assert "alert.name: DLQEventsDetected" in bm25.search.call_args.args[0]
    assert "unsupported event type Trip.Requested.v2" in reranker.rerank.call_args.args[0]


def test_projection_preserves_exact_oom_signal():
    document = replace(candidate("oom").document, trigger="OOMKilled", conclusive=True)
    snapshot = ApprovedCorpusSnapshot(9, (document,), {"OOMKilled": (document,)}, StaticBM25())

    result = service(snapshot, Mock()).retrieve(
        build_evidence_projection("OOM", {"last_terminated_reason": "OOMKilled"}),
    )

    assert result.mode is RetrievalMode.EXACT_CONCLUSIVE
    assert result.candidate.knowledge_key == "oom"


def test_missing_corpus_degrades_without_a_legacy_fallback():
    provider = Mock(get_snapshot=Mock(side_effect=CorpusUnavailable("redis down")))

    result = RetrievalService(provider, Mock()).retrieve(build_evidence_projection("Alert", {}))

    assert result.mode is RetrievalMode.DEGRADED
    assert result.degraded_reason == "corpus_unavailable"
