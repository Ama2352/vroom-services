from dataclasses import dataclass

from evidence_projection import normalize_evidence
from retrieval.evidence import EvidenceRetrievalMode, EvidenceRetrievalService


@dataclass
class FakeCorpus:
    documents: list

    def get_documents(self):
        return self.documents


class FakeReranker:
    def rerank(self, query, candidates):
        return tuple(candidates)


def template(log_error="unknown event type Trip.Requested.v2"):
    return normalize_evidence(
        {"alert_name": "DLQEventsDetected", "service": "dispatch-service"},
        {},
        {"status": "found", "message": log_error},
        {"status": "correlated", "error_service": "dispatch-service", "error_operation": "dispatch.consume"},
        {},
    )


def document(key, template_value, exact=True, hints=None):
    return {
        "example_id": f"example-{key}",
        "knowledge_key": key,
        "fingerprint": template_value.fingerprint(),
        "exact_reusable": exact,
        "evidence_text": template_value.serialize(),
        "hint_texts": hints or ["unsupported event contract"],
    }


def test_unique_identical_reusable_example_is_exact():
    live = template()
    service = EvidenceRetrievalService(FakeCorpus([document("unsupported_event_contract", live)]), FakeReranker())
    result = service.retrieve(live)
    assert result.mode is EvidenceRetrievalMode.EXACT
    assert [item.knowledge_key for item in result.candidates] == ["unsupported_event_contract"]


def test_advisory_returns_at_most_three_distinct_families_and_excludes_answers():
    live = template()
    docs = [document(f"case_{i}", template(f"error variant {i}"), exact=False) for i in range(5)]
    service = EvidenceRetrievalService(FakeCorpus(docs), FakeReranker())
    result = service.retrieve(live)
    assert result.mode is EvidenceRetrievalMode.NEAREST
    assert len(result.candidates) == 3
    assert all("diagnosis_cause" not in item.serialized for item in result.candidates)


def test_advisory_does_not_return_examples_that_only_share_template_field_names():
    live = template()
    relevant = document("event_contract", template("unknown event type Trip.Requested.v1"), exact=False)
    unrelated = document("redis", normalize_evidence(
        {"alert_name": "ServiceDown", "service": "ride-service"}, {},
        {"status": "found", "message": "redis connection refused"}, {}, {},
    ), exact=False, hints=["redis endpoint configuration"])
    result = EvidenceRetrievalService(FakeCorpus([relevant, unrelated]), FakeReranker()).retrieve(live)
    assert [candidate.knowledge_key for candidate in result.candidates] == ["event_contract"]
