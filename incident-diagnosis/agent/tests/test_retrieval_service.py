from dataclasses import replace
from unittest.mock import Mock

from retrieval.bm25 import BM25Index
from retrieval.corpus import ApprovedCorpusSnapshot, CorpusUnavailable
from retrieval.models import (
    RankedCandidate, RetrievalDocument, RetrievalMode,
)
from retrieval.reranker import ScoredCandidate
from retrieval.service import RetrievalService
from retrieval.signals import serialize_incident
from evidence_projection import build_evidence_projection
from routing import RoutingDecision


class StaticBM25:
    def __init__(self, candidates=()):
        self.candidates = tuple(candidates)

    def search(self, query, limit=8):
        return self.candidates[:limit]


class StaticProvider:
    def __init__(self, snapshot, stale=False):
        self.snapshot = snapshot
        self.stale = stale

    def get_snapshot(self):
        return self.snapshot, self.stale


class FakeReranker:
    def __init__(self, scores):
        self.scores = scores

    def rerank(self, query, candidates):
        scored = [ScoredCandidate(c, float(self.scores[c.knowledge_key])) for c in candidates]
        return tuple(sorted(
            scored,
            key=lambda item: (
                -item.score, -item.candidate.bm25_score,
                item.candidate.knowledge_key, item.candidate.document.source_id,
            ),
        ))


def document(key, conclusive=False):
    return RetrievalDocument(
        knowledge_key=key, source="knowledge", source_id=key,
        trigger="", conclusive=conclusive,
        root_cause_pattern=f"{key} root cause", fix_action=f"fix {key}",
        document_text=f"{key} evidence",
    )


def candidate(key, bm25_score=1.0):
    return RankedCandidate(document(key), bm25_score=bm25_score)


def make_snapshot(exact_by_trigger=None, candidates=()):
    return ApprovedCorpusSnapshot(
        version=9,
        documents=tuple(item.document for item in candidates),
        exact_by_trigger=exact_by_trigger or {},
        bm25=StaticBM25(candidates),
    )


def snapshot_with_exact(trigger, doc):
    exact_doc = replace(doc, trigger=trigger, conclusive=True)
    return make_snapshot({trigger: (exact_doc,)})


def snapshot_with_two_exact(trigger, first, second):
    first_doc = replace(document(first), trigger=trigger, conclusive=True)
    second_doc = replace(document(second), trigger=trigger, conclusive=True)
    return make_snapshot(
        {trigger: (first_doc, second_doc)},
        (candidate(first, 2.0), candidate(second, 1.0)),
    )


def snapshot_with_bm25_candidates(*keys):
    return make_snapshot(candidates=tuple(
        candidate(key, float(len(keys) - index)) for index, key in enumerate(keys)
    ))


def empty_snapshot():
    return make_snapshot()


def service_for(snapshot, reranker):
    return RetrievalService(StaticProvider(snapshot), reranker)


def rich_facts():
    return {"log_error": "specific incident evidence"}


def routing_decision():
    return RoutingDecision(
        incident_kind="dlq",
        evidence_chain={},
        primary_signals=("primary contract evidence",),
        secondary_signals=("secondary readiness context",),
        reason_codes=("explicit_incident_kind",),
    )


def test_unique_conclusive_exact_match_bypasses_bm25_and_reranker():
    reranker = Mock()
    result = service_for(
        snapshot_with_exact("OOMKilled", document("oom", conclusive=True)), reranker,
    ).retrieve(
        "Terminated", {"last_terminated_reason": "OOMKilled"},
        routing_decision(),
    )
    assert result.mode is RetrievalMode.EXACT_CONCLUSIVE
    assert result.candidate.knowledge_key == "oom"
    reranker.rerank.assert_not_called()


def test_legacy_routing_is_ignored_in_favor_of_neutral_facts():
    bm25 = Mock(search=Mock(return_value=(candidate("contract"),)))
    snapshot = replace(make_snapshot(), bm25=bm25)
    reranker = Mock(rerank=Mock(return_value=(
        ScoredCandidate(candidate("contract"), 2.0),
    )))

    result = service_for(snapshot, reranker).retrieve(
        "Alert", rich_facts(), routing_decision(),
    )

    assert result.mode is RetrievalMode.RERANKED_ADVISORY
    assert bm25.search.call_args.args[0] == serialize_incident("Alert", rich_facts())
    assert reranker.rerank.call_args.args[0] == "specific incident evidence"


def test_missing_routing_uses_legacy_serializers():
    bm25 = Mock(search=Mock(return_value=()))
    snapshot = replace(make_snapshot(), bm25=bm25)

    service_for(snapshot, Mock()).retrieve("Alert", rich_facts())

    assert bm25.search.call_args.args[0] == serialize_incident("Alert", rich_facts())


def test_multiple_exact_groups_are_ambiguous_and_continue_to_reranking():
    reranker = FakeReranker({"first": -2.0, "second": 3.0})
    result = service_for(
        snapshot_with_two_exact("CrashLoopBackOff", "first", "second"), reranker,
    ).retrieve("Waiting", {"waiting_reason": "CrashLoopBackOff"})
    assert result.mode is RetrievalMode.RERANKED_ADVISORY
    assert result.candidate.knowledge_key == "second"
    assert result.exact_ambiguous is True


def test_only_reranked_rank_one_can_be_accepted():
    result = service_for(
        snapshot_with_bm25_candidates("first", "second"),
        FakeReranker({"first": 1.0, "second": 4.0}),
    ).retrieve("Alert", rich_facts())
    assert result.mode is RetrievalMode.RERANKED_ADVISORY
    assert result.candidate.knowledge_key == "second"
    assert result.reranker_score == 4.0


def test_rank_one_below_frozen_floor_returns_none():
    result = service_for(
        snapshot_with_bm25_candidates("first"), FakeReranker({"first": 1.19}),
    ).retrieve("Alert", rich_facts())
    assert result.mode is RetrievalMode.NONE
    assert result.accepted is False


def test_no_bm25_candidates_returns_none_without_reranking():
    reranker = Mock()
    result = service_for(empty_snapshot(), reranker).retrieve("Alert", {})
    assert result.mode is RetrievalMode.NONE
    reranker.rerank.assert_not_called()


def test_missing_first_snapshot_is_degraded():
    provider = Mock(get_snapshot=Mock(side_effect=CorpusUnavailable("redis down")))
    result = RetrievalService(provider, FakeReranker({})).retrieve("Alert", {})
    assert result.mode is RetrievalMode.DEGRADED
    assert result.degraded_reason == "corpus_unavailable"


def test_minilm_failure_never_falls_back_to_bm25_acceptance():
    reranker = Mock(rerank=Mock(side_effect=RuntimeError("model unavailable")))
    result = service_for(
        snapshot_with_bm25_candidates("image_pull"), reranker,
    ).retrieve("Alert", rich_facts())
    assert result.mode is RetrievalMode.DEGRADED
    assert result.accepted is False
    assert result.candidate is None


def test_stale_snapshot_marks_debug_state_but_can_retrieve():
    provider = StaticProvider(
        snapshot_with_exact("OOMKilled", document("oom")), stale=True,
    )
    result = RetrievalService(provider, FakeReranker({})).retrieve(
        "Alert", {"last_terminated_reason": "OOMKilled"},
    )
    assert result.mode is RetrievalMode.EXACT_CONCLUSIVE
    assert result.stale_snapshot is True


def test_projection_is_the_neutral_retrieval_input():
    bm25 = Mock(search=Mock(return_value=(candidate("contract"),)))
    snapshot = replace(make_snapshot(), bm25=bm25)
    reranker = Mock(rerank=Mock(return_value=(
        ScoredCandidate(candidate("contract"), 2.0),
    )))
    projection = build_evidence_projection(
        "DLQEventsDetected",
        {"log_error": "unsupported event type Trip.Requested.v2"},
    )

    result = service_for(snapshot, reranker).retrieve(projection)

    assert result.mode is RetrievalMode.RERANKED_ADVISORY
    query = bm25.search.call_args.args[0]
    reranker_query = reranker.rerank.call_args.args[0]
    assert "alert.name: DLQEventsDetected" in query
    assert "runtime.log_error: unsupported event type Trip.Requested.v2" in query
    assert "primary" not in query.lower()
    assert "incident_kind" not in query.lower()
    assert "unsupported event type Trip.Requested.v2" in reranker_query


def test_projection_preserves_exact_oom_signal():
    projection = build_evidence_projection(
        "IncidentAgentExactOOMTest",
        {"last_terminated_reason": "OOMKilled"},
    )
    result = service_for(
        snapshot_with_exact("OOMKilled", document("oom", conclusive=True)),
        Mock(),
    ).retrieve(projection)
    assert result.mode is RetrievalMode.EXACT_CONCLUSIVE
