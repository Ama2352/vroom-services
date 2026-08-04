import hashlib
from pathlib import Path

import pytest

from evaluation.local_reranker import rerank_local
from evaluation.model_artifacts import (
    ModelSpec,
    ensure_model_artifact,
    load_manifest,
    verify_sha256,
)
from evaluation.models import RankedCandidate, RetrievalCase, RetrievalOutcome


class FakeBackend:
    def __init__(self, scores):
        self.scores = tuple(scores)
        self.calls = []

    def score(self, query, documents):
        self.calls.append((query, tuple(documents)))
        return self.scores


@pytest.fixture
def batch_factory():
    def make(scores=(8.0, 7.0, 6.0), mode="advisory", keys=None):
        keys = keys or tuple(f"k{index}" for index in range(len(scores)))
        case = RetrievalCase(
            "case", "calibration", "PodUnavailable",
            {"waiting_reason": "CrashLoopBackOff"}, (), mode, (), "test",
        )
        candidates = tuple(
            RankedCandidate(
                key, score, "knowledge", f"source-{index}", (),
                f"cause-{index}", f"fix-{index}", document_text=f"document-{index}",
            )
            for index, (key, score) in enumerate(zip(keys, scores))
        )
        return case, RetrievalOutcome(mode, candidates)

    return make


def test_manifest_loads_the_two_pinned_apache_models():
    specs = load_manifest()
    assert specs == (
        ModelSpec(
            "minilm", "cross-encoder/ms-marco-MiniLM-L6-v2",
            "eeed17e3bfc6fa06a790f2d12a9501fec587fccf",
            "onnx/model_quint8_avx2.onnx",
            "c80a8b34256ea453093d612e3ac48d3d965a0c0a48c7906709af8b8e28461bf9",
            512, "apache-2.0",
        ),
        ModelSpec(
            "mixedbread_xsmall", "mixedbread-ai/mxbai-rerank-xsmall-v1",
            "d8e18fdfcfc8b37c036c5c23e9fa9bda8d738cc9",
            "onnx/model_quantized.onnx",
            "15ef19a6de90be7d52b627f2c784107bd806e64826450f41fb75fa4f0179ab30",
            512, "apache-2.0",
        ),
    )


def test_checksum_verifier_rejects_wrong_artifact(tmp_path):
    artifact = tmp_path / "model.onnx"
    artifact.write_bytes(b"wrong")
    spec = ModelSpec("test", "repo", "revision", "model.onnx", "0" * 64, 512)
    with pytest.raises(ValueError, match="checksum"):
        verify_sha256(artifact, spec.sha256)


def test_artifact_download_is_pinned_narrow_and_checksum_enforced(tmp_path, monkeypatch):
    spec = ModelSpec(
        "test", "owner/repo", "fixed-revision", "onnx/model.onnx",
        hashlib.sha256(b"verified").hexdigest(), 512,
    )
    calls = []

    def snapshot_download(**kwargs):
        calls.append(kwargs)
        artifact = Path(kwargs["local_dir"]) / spec.onnx_file
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"verified")
        return str(kwargs["local_dir"])

    monkeypatch.setattr("evaluation.model_artifacts._snapshot_download", snapshot_download)
    artifact = ensure_model_artifact(spec, tmp_path)

    assert artifact == tmp_path / "onnx" / "model.onnx"
    assert calls == [{
        "repo_id": "owner/repo",
        "revision": "fixed-revision",
        "local_dir": tmp_path,
        "allow_patterns": (
            "config.json", "tokenizer.json", "tokenizer_config.json",
            "special_tokens_map.json", "vocab.txt", "spm.model", "onnx/model.onnx",
        ),
    }]


def test_corrupt_artifact_removes_only_the_exact_model_file(tmp_path, monkeypatch):
    spec = ModelSpec("test", "repo", "revision", "onnx/model.onnx", "0" * 64, 512)
    artifact = tmp_path / spec.onnx_file
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"wrong")
    neighbor = tmp_path / "keep.txt"
    neighbor.write_text("keep")
    monkeypatch.setattr(
        "evaluation.model_artifacts._snapshot_download",
        lambda **kwargs: pytest.fail("corrupt existing model must not download"),
    )

    with pytest.raises(ValueError, match="checksum"):
        ensure_model_artifact(spec, tmp_path)

    assert not artifact.exists()
    assert neighbor.read_text() == "keep"


def test_local_reranker_sorts_scores_applies_floor_and_keeps_bm25_tie_break(batch_factory):
    backend = FakeBackend(scores=(0.7, 0.7, 0.2))
    trace = rerank_local(batch_factory(scores=(8.0, 7.0, 6.0)), backend, floor=0.5)

    assert [item.knowledge_key for item in trace.outcome.candidates] == ["k0", "k1"]
    assert [decision.accepted for decision in trace.decisions] == [True, True, False]
    assert trace.decisions[0].score == 0.7
    assert len(backend.calls) == 1
    assert trace.latency_ms >= 0.0


def test_local_reranker_collapses_knowledge_keys_after_deterministic_sort(batch_factory):
    backend = FakeBackend(scores=(0.7, 0.9, 0.8))
    trace = rerank_local(
        batch_factory(scores=(8.0, 3.0, 7.0), keys=("same", "same", "other")),
        backend,
        floor=0.5,
    )

    assert [(item.knowledge_key, item.source_id) for item in trace.outcome.candidates] == [
        ("same", "source-1"), ("other", "source-2"),
    ]
    assert len(trace.decisions) == 3


def test_exact_batch_bypasses_backend(batch_factory):
    backend = FakeBackend(scores=())
    batch = batch_factory(scores=(8.0,), mode="exact")
    trace = rerank_local(batch, backend, floor=99.0)

    assert trace.outcome == batch[1]
    assert [(decision.accepted, decision.score) for decision in trace.decisions] == [
        (True, None),
    ]
    assert "exact" in trace.decisions[0].reason
    assert trace.latency_ms == 0.0
    assert backend.calls == []


def test_empty_batch_bypasses_backend(batch_factory):
    backend = FakeBackend(scores=())
    batch = batch_factory(scores=(), keys=())
    trace = rerank_local(batch, backend, floor=0.5)

    assert trace.outcome == batch[1]
    assert trace.decisions == ()
    assert backend.calls == []


@pytest.mark.model
@pytest.mark.parametrize("model_name", ("minilm", "mixedbread_xsmall"))
def test_pinned_model_prefers_relevant_document(model_name, tmp_path):
    from evaluation.onnx_backend import OnnxCrossEncoder

    spec = next(spec for spec in load_manifest() if spec.name == model_name)
    artifact = ensure_model_artifact(spec, tmp_path / model_name)
    backend = OnnxCrossEncoder(artifact.parents[len(Path(spec.onnx_file).parts) - 1], spec)
    scores = backend.score(
        "pod is crash looping because it exceeded its memory limit",
        (
            "OOMKilled means the container exceeded its memory limit.",
            "DNS lookup failures usually indicate an unavailable name server.",
        ),
    )
    assert scores[0] > scores[1]
