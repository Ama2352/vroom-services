from pathlib import Path

import pytest

from retrieval.models import RankedCandidate, RetrievalDocument
from retrieval.reranker import (
    MiniLMReranker, load_model_spec, serialize_candidate, verify_sha256,
)


MANIFEST = Path(__file__).parents[1] / "retrieval" / "model_manifest.json"


class FakeBackend:
    def __init__(self, scores):
        self.scores = tuple(scores)

    def score(self, query, documents):
        return self.scores


def make_ranked_candidate(key="image_pull", text="manifest tag not found", source="history"):
    document = RetrievalDocument(
        knowledge_key=key, source=source,
        source_id=f"{source}-{key}", trigger="", conclusive=False,
        root_cause_pattern=f"{key} root cause", fix_action=f"fix {key}",
        document_text=text,
        context_notes="approved occurrence" if source == "history" else "",
    )
    return RankedCandidate(document, bm25_score=1.0)


def make_three_candidates():
    return (
        make_ranked_candidate("first", "first document", "knowledge"),
        make_ranked_candidate("second", "second document", "knowledge"),
        make_ranked_candidate("third", "third document", "knowledge"),
    )


def test_manifest_is_the_verified_tournament_artifact():
    spec = load_model_spec(MANIFEST)
    assert spec.repo_id == "cross-encoder/ms-marco-MiniLM-L6-v2"
    assert spec.revision == "eeed17e3bfc6fa06a790f2d12a9501fec587fccf"
    assert spec.sha256 == "c80a8b34256ea453093d612e3ac48d3d965a0c0a48c7906709af8b8e28461bf9"
    assert len(spec.sha256) == 64


def test_verify_sha256_rejects_wrong_file(tmp_path):
    artifact = tmp_path / "model.onnx"
    artifact.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="checksum"):
        verify_sha256(artifact, "0" * 64)


def test_candidate_serialization_preserves_labels():
    text = serialize_candidate(make_ranked_candidate())
    assert "knowledge_key: image_pull" in text
    assert "document: manifest tag not found" in text
    assert "root_cause_pattern:" in text
    assert "fix_action:" in text
    assert "approved_history_context:" in text


def test_reranker_orders_all_candidates_by_model_score():
    backend = FakeBackend(scores=(-4.0, 3.2, 1.5))
    ranked = MiniLMReranker(backend).rerank("alert_name: Test", make_three_candidates())
    assert [item.candidate.knowledge_key for item in ranked] == ["second", "third", "first"]
    assert [item.score for item in ranked] == [3.2, 1.5, -4.0]


def test_backend_score_count_must_match_candidates():
    reranker = MiniLMReranker(FakeBackend(scores=(1.0,)))
    with pytest.raises(ValueError, match="score count"):
        reranker.rerank("query", make_three_candidates())
