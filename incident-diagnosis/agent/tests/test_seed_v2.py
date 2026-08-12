import fakeredis

from evidence_projection import normalize_evidence
from retrieval.evidence import EvidenceRetrievalMode, EvidenceRetrievalService
from retrieval.v2_corpus import RedisEvidenceCorpus
from seed import seed_v2_if_empty


class IdentityReranker:
    def rerank(self, _query, candidates):
        return candidates


def test_seeded_dlq_example_is_exact_after_current_evidence_is_collected():
    rdb = fakeredis.FakeRedis(decode_responses=True)
    assert seed_v2_if_empty(rdb) == 3
    template = normalize_evidence(
        {"alert_name": "DLQEventsDetected", "service": "dispatch-service", "metric_value": 1.08}, {},
        {"status": "found", "message": 'unknown event type "Trip.Requested.v2"'},
        {"status": "correlated", "error_service": "dispatch-service",
         "error_operation": "dispatch.consume.Trip.Requested.v2",
         "error_message": 'unknown event type "Trip.Requested.v2"'}, {},
    )
    result = EvidenceRetrievalService(RedisEvidenceCorpus(rdb), IdentityReranker()).retrieve(template)
    assert result.mode is EvidenceRetrievalMode.EXACT
    assert result.candidates[0].knowledge_key == "unsupported_event_contract"


def test_unknown_kubernetes_termination_value_does_not_break_exact_template_reuse():
    rdb = fakeredis.FakeRedis(decode_responses=True)
    seed_v2_if_empty(rdb)
    template = normalize_evidence(
        {"alert_name": "DLQEventsDetected", "service": "dispatch-service"},
        {"last_terminated_reason": "Unknown"},
        {"status": "found", "message": 'unknown event type "Trip.Requested.v2"'},
        {"status": "correlated", "error_service": "dispatch-service",
         "error_operation": "dispatch.consume.Trip.Requested.v2",
         "error_message": 'unknown event type "Trip.Requested.v2"'}, {},
    )
    result = EvidenceRetrievalService(RedisEvidenceCorpus(rdb), IdentityReranker()).retrieve(template)
    assert result.mode is EvidenceRetrievalMode.EXACT
