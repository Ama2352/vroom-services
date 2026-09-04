"""Offline-only MiniLM adapter used by the model-selection notebooks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

from retrieval.models import EvidenceCandidate


@dataclass(frozen=True)
class ModelSpec:
    name: str
    repo_id: str
    revision: str
    onnx_file: str
    sha256: str
    license: str
    max_length: int


def load_model_spec(path: Path) -> ModelSpec:
    return ModelSpec(**json.loads(path.read_text(encoding="utf-8")))


def verify_sha256(artifact: Path, expected_sha256: str) -> Path:
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"model checksum mismatch: expected {expected_sha256}, got {digest}")
    return artifact


class OnnxCrossEncoder:
    """Load and score a pinned local ONNX model for offline comparison only."""

    def __init__(self, model_dir: Path, spec: ModelSpec):
        from transformers import AutoTokenizer
        import onnxruntime

        self.spec = spec
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_dir, local_files_only=True, revision=spec.revision,
        )
        artifact = verify_sha256(model_dir / spec.onnx_file, spec.sha256)
        self.session = onnxruntime.InferenceSession(str(artifact), providers=["CPUExecutionProvider"])
        self.input_names = {item.name for item in self.session.get_inputs()}

    def score(self, query: str, documents: tuple[str, ...]) -> tuple[float, ...]:
        if not documents:
            return ()
        import numpy

        encoded = self.tokenizer(
            [query] * len(documents), list(documents), padding=True,
            truncation=True, max_length=self.spec.max_length, return_tensors="np",
        )
        feeds = {name: value for name, value in encoded.items() if name in self.input_names}
        scores = tuple(float(value) for value in numpy.asarray(self.session.run(None, feeds)[0]).reshape(-1))
        if len(scores) != len(documents):
            raise ValueError("reranker score count does not match candidates")
        return scores


class MiniLMReranker:
    """Rank BM25 candidates for the offline reranking comparison."""

    def __init__(self, backend):
        self.backend = backend

    def rerank(
        self, query: str, candidates: tuple[EvidenceCandidate, ...],
    ) -> tuple[EvidenceCandidate, ...]:
        if not candidates:
            return ()
        scores = self.backend.score(query, tuple(item.serialized for item in candidates))
        if len(scores) != len(candidates):
            raise ValueError("reranker score count does not match candidates")
        scored = tuple(replace(item, reranker_score=float(score)) for item, score in zip(candidates, scores))
        highest = max(item.reranker_score for item in scored)
        return tuple(sorted(
            scored,
            key=lambda item: (
                -(item.bm25_score if highest - item.reranker_score <= 0.5 else 0.0),
                -item.reranker_score,
                item.knowledge_key,
                item.example_id,
            ),
        ))
