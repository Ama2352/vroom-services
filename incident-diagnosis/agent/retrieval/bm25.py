"""Lexical candidate generation for evidence-first retrieval."""

from __future__ import annotations

import math
import re
from dataclasses import replace

from rank_bm25 import BM25Okapi

from .models import EvidenceCandidate


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def evidence_values(serialized: str) -> str:
    """Search observation values, not shared labels such as `service`."""
    values = []
    for line in serialized.splitlines():
        if ":" not in line:
            continue
        _, value = line.split(":", 1)
        if value.strip():
            values.append(value.strip())
    return "\n".join(values)


class PositiveIdfBM25(BM25Okapi):
    """BM25 with positive IDF smoothing for a small incident corpus."""

    def _calc_idf(self, nd):
        for word, frequency in nd.items():
            self.idf[word] = math.log(
                1 + (self.corpus_size - frequency + 0.5) / (frequency + 0.5)
            )


class BM25Index:
    def __init__(self, candidates: tuple[EvidenceCandidate, ...] | list[EvidenceCandidate]):
        self.candidates = tuple(candidates)
        self._tokenized = tuple(tokenize(evidence_values(item.serialized)) for item in self.candidates)
        self._bm25 = PositiveIdfBM25(list(self._tokenized)) if self._tokenized else None

    def search(self, query: str, limit: int = 8) -> tuple[EvidenceCandidate, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if not self._bm25 or not query.strip():
            return ()

        query_tokens = list(dict.fromkeys(tokenize(query)))
        if not query_tokens:
            return ()
        query_terms = set(query_tokens)
        # BM25 provides the lexical candidate order used by the agent.
        ranked = []
        for candidate, document_tokens, score in zip(
            self.candidates, self._tokenized, self._bm25.get_scores(query_tokens),
        ):
            if float(score) <= 0:
                continue
            ranked.append(replace(
                candidate,
                bm25_score=float(score),
                matched_terms=tuple(sorted(query_terms.intersection(document_tokens))),
            ))

        ranked.sort(key=lambda item: (-item.bm25_score, item.knowledge_key, item.example_id))
        # Keep one representative example per incident family.
        families: dict[str, EvidenceCandidate] = {}
        for candidate in ranked:
            families.setdefault(candidate.knowledge_key, candidate)
        return tuple(families.values())[:limit]
