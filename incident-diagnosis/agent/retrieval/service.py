from __future__ import annotations

import time
from pathlib import Path

from .corpus import CorpusProvider, CorpusUnavailable
from .models import ACCEPTANCE_FLOOR, RetrievalMode, RetrievalResult
from .reranker import MiniLMReranker, OnnxCrossEncoder, load_model_spec
from .signals import (
    extract_canonical_signals,
    serialize_incident,
    serialize_reranker_query,
    serialize_routed_incident,
    serialize_routed_reranker_query,
)


class UnavailableReranker:
    def __init__(self, reason: str):
        self.reason = reason

    def rerank(self, query, candidates):
        raise RuntimeError(self.reason)


class RetrievalService:
    def __init__(self, corpus: CorpusProvider, reranker):
        self.corpus = corpus
        self.reranker = reranker

    def retrieve(self, alert_name: str, facts: dict, routing=None) -> RetrievalResult:
        started = time.perf_counter()
        bm25_ms = None
        reranker_ms = None
        try:
            snapshot, stale = self.corpus.get_snapshot()
        except CorpusUnavailable:
            return self._finish(
                RetrievalResult.degraded("corpus_unavailable"), started,
                bm25_ms, reranker_ms,
            )

        signals = extract_canonical_signals(facts)
        exact = {
            document.knowledge_key: document
            for signal in signals
            for document in snapshot.exact_by_trigger.get(signal, ())
        }
        if len(exact) == 1:
            result = RetrievalResult.exact_conclusive(
                next(iter(exact.values())), snapshot.version,
                stale_snapshot=stale,
            )
            return self._finish(result, started, bm25_ms, reranker_ms)

        if routing is not None and (
                getattr(routing, "primary_signals", ())
                or getattr(routing, "secondary_signals", ())):
            query = serialize_routed_incident(routing)
            reranker_query = serialize_routed_reranker_query(routing)
        else:
            query = serialize_incident(alert_name, facts)
            reranker_query = serialize_reranker_query(alert_name, facts)
        bm25_started = time.perf_counter()
        try:
            candidates = snapshot.bm25.search(query, limit=8)
        except Exception:
            result = RetrievalResult.degraded(
                "bm25_failed", snapshot.version, stale_snapshot=stale,
            )
            return self._finish(
                result, started,
                (time.perf_counter() - bm25_started) * 1000,
                reranker_ms,
            )
        bm25_ms = (time.perf_counter() - bm25_started) * 1000
        if not candidates:
            result = RetrievalResult.none(
                snapshot.version,
                stale_snapshot=stale,
                exact_ambiguous=len(exact) > 1,
            )
            return self._finish(result, started, bm25_ms, reranker_ms)

        reranker_started = time.perf_counter()
        try:
            reranked = self.reranker.rerank(reranker_query, candidates)
        except Exception:
            result = RetrievalResult.degraded(
                "reranker_failed", snapshot.version, stale_snapshot=stale,
            )
            return self._finish(
                result, started, bm25_ms,
                (time.perf_counter() - reranker_started) * 1000,
            )
        reranker_ms = (time.perf_counter() - reranker_started) * 1000
        if not reranked:
            result = RetrievalResult.none(
                snapshot.version,
                stale_snapshot=stale,
                exact_ambiguous=len(exact) > 1,
            )
            return self._finish(result, started, bm25_ms, reranker_ms)

        top = reranked[0]
        ranking = tuple(
            (item.candidate.knowledge_key, item.candidate.bm25_score, item.score)
            for item in reranked
        )
        if top.score < ACCEPTANCE_FLOOR:
            result = RetrievalResult.none(
                snapshot.version,
                stale_snapshot=stale,
                exact_ambiguous=len(exact) > 1,
                ranking=ranking,
            )
        else:
            result = RetrievalResult.accepted_advisory(
                top.candidate,
                top.score,
                snapshot.version,
                stale_snapshot=stale,
                exact_ambiguous=len(exact) > 1,
                ranking=ranking,
            )
        return self._finish(result, started, bm25_ms, reranker_ms)

    @staticmethod
    def _finish(result, started, bm25_ms, reranker_ms):
        print(
            f"[retrieval] mode={result.mode.value} accepted={result.accepted} "
            f"corpus_version={result.corpus_version} stale={result.stale_snapshot} "
            f"bm25_ms={bm25_ms if bm25_ms is not None else '-'} "
            f"reranker_ms={reranker_ms if reranker_ms is not None else '-'} "
            f"failure={result.degraded_reason or '-'}",
            flush=True,
        )
        return result


def create_retrieval_service(rdb, model_dir: Path) -> RetrievalService:
    provider = CorpusProvider(rdb)
    manifest = Path(__file__).with_name("model_manifest.json")
    try:
        spec = load_model_spec(manifest)
        backend = OnnxCrossEncoder(model_dir, spec)
        reranker = MiniLMReranker(backend)
        print(
            f"[retrieval] reranker_loaded model={spec.name} revision={spec.revision}",
            flush=True,
        )
    except Exception as exc:
        print(
            f"[retrieval] reranker_unavailable error={type(exc).__name__}",
            flush=True,
        )
        reranker = UnavailableReranker(type(exc).__name__)
    return RetrievalService(provider, reranker)
