from __future__ import annotations

import math
import re

from rank_bm25 import BM25Okapi

from .models import RankedCandidate, RetrievalDocument


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


class PositiveIdfBM25(BM25Okapi):
    """BM25Okapi with strictly positive Lucene-style IDF smoothing."""

    def _calc_idf(self, nd):
        for word, freq in nd.items():
            self.idf[word] = math.log(
                1 + (self.corpus_size - freq + 0.5) / (freq + 0.5)
            )


class BM25Index:
    def __init__(self, documents: tuple[RetrievalDocument, ...] | list[RetrievalDocument]):
        self.documents = tuple(documents)
        self._tokenized = tuple(tokenize(doc.document_text) for doc in self.documents)
        self._bm25 = (
            PositiveIdfBM25(list(self._tokenized)) if self._tokenized else None
        )

    def search(self, query: str, limit: int = 8) -> tuple[RankedCandidate, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if not self.documents or not query.strip() or self._bm25 is None:
            return ()

        query_tokens = list(dict.fromkeys(tokenize(query)))
        if not query_tokens:
            return ()
        scores = self._bm25.get_scores(query_tokens)
        query_terms = set(query_tokens)
        ranked: list[RankedCandidate] = []
        for document, document_tokens, score in zip(
            self.documents, self._tokenized, scores,
        ):
            raw_score = float(score)
            if raw_score <= 0:
                continue
            ranked.append(RankedCandidate(
                document=document,
                bm25_score=raw_score,
                matched_terms=tuple(sorted(query_terms.intersection(document_tokens))),
            ))

        ranked.sort(key=lambda item: (
            -item.bm25_score,
            item.knowledge_key,
            item.document.source,
            item.document.document_text,
            item.document.source_id,
        ))
        collapsed: dict[str, RankedCandidate] = {}
        for candidate in ranked:
            collapsed.setdefault(candidate.knowledge_key, candidate)
        return tuple(collapsed.values())[:limit]
