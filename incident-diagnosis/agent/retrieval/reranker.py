from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .models import RankedCandidate, RetrievalDocument


@dataclass(frozen=True)
class ModelSpec:
    name: str
    repo_id: str
    revision: str
    onnx_file: str
    sha256: str
    license: str
    max_length: int


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: RankedCandidate
    score: float


def load_model_spec(path: Path) -> ModelSpec:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ModelSpec(**raw)


def verify_sha256(artifact: Path, expected_sha256: str) -> Path:
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError(
            f"model checksum mismatch: expected {expected_sha256}, got {digest}"
        )
    return artifact


def _clean(value: str) -> str:
    return " ".join(str(value).replace("\x00", " ").split())


def serialize_candidate(candidate: RankedCandidate | RetrievalDocument) -> str:
    document = candidate.document if isinstance(candidate, RankedCandidate) else candidate
    lines = [
        f"knowledge_key: {_clean(document.knowledge_key)}",
        f"document: {_clean(document.document_text)}",
        f"root_cause_pattern: {_clean(document.root_cause_pattern)}",
        f"fix_action: {_clean(document.fix_action)}",
    ]
    if document.context_notes:
        lines.append(f"approved_history_context: {_clean(document.context_notes)}")
    return "\n".join(lines)


class OnnxCrossEncoder:
    def __init__(self, model_dir: Path, spec: ModelSpec):
        from transformers import AutoTokenizer
        import onnxruntime

        self.spec = spec
        self.model_dir = model_dir
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_dir, local_files_only=True, revision=spec.revision,
        )
        artifact = verify_sha256(model_dir / spec.onnx_file, spec.sha256)
        self.session = onnxruntime.InferenceSession(
            str(artifact), providers=["CPUExecutionProvider"],
        )
        self.input_names = {item.name for item in self.session.get_inputs()}

    def score(self, query: str, documents: tuple[str, ...]) -> tuple[float, ...]:
        if not documents:
            return ()
        import numpy

        encoded = self.tokenizer(
            [query] * len(documents), list(documents), padding=True,
            truncation=True, max_length=self.spec.max_length, return_tensors="np",
        )
        feeds = {
            name: value for name, value in encoded.items()
            if name in self.input_names
        }
        logits = self.session.run(None, feeds)[0]
        scores = tuple(float(value) for value in numpy.asarray(logits).reshape(-1))
        if len(scores) != len(documents):
            raise ValueError("ONNX output count does not match documents")
        return scores


class MiniLMReranker:
    def __init__(self, backend):
        self.backend = backend

    def rerank(
        self, query: str, candidates: tuple[RankedCandidate, ...],
    ) -> tuple[ScoredCandidate, ...]:
        if not candidates:
            return ()
        documents = tuple(serialize_candidate(candidate) for candidate in candidates)
        scores = self.backend.score(query, documents)
        if len(scores) != len(candidates):
            raise ValueError("reranker score count does not match candidates")
        scored = tuple(
            ScoredCandidate(candidate, float(score))
            for candidate, score in zip(candidates, scores)
        )
        highest = max(item.score for item in scored)
        # Cross-encoder scores are noisy for near-paraphrases. When the top
        # scores are within a small margin, retain BM25's lexical evidence as
        # a precision-preserving tie-breaker instead of making a semantic-only
        # flip between almost indistinguishable candidates.
        return tuple(sorted(
            scored,
            key=lambda item: (
                -(item.candidate.bm25_score if highest - item.score <= 0.5 else 0.0),
                -item.score,
                item.candidate.knowledge_key,
                item.candidate.document.source,
                item.candidate.document.source_id,
            ),
        ))
