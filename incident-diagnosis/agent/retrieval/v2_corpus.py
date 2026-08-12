"""Redis-backed evidence-only corpus and MiniLM adapter for the live v2 path."""

from __future__ import annotations

from evidence_projection import TEMPLATE_FIELDS
from memory import get_knowledge_v2, list_examples_v2, list_knowledge_hint_ids_v2, search_hints_v2


class RedisEvidenceCorpus:
    def __init__(self, rdb):
        self.rdb = rdb

    def get_documents(self):
        hints = {item["hint_id"]: item["text"] for item in search_hints_v2(self.rdb)}
        documents = []
        for example in list_examples_v2(self.rdb):
            evidence = example.get("evidence") or {}
            if isinstance(evidence, str):
                evidence_text = evidence
            else:
                evidence_text = "\n".join(f"{field}: {evidence.get(field, '')}" for field in TEMPLATE_FIELDS)
            knowledge_key = example["knowledge_key"]
            hint_ids = set(example.get("hint_ids") or ()) | set(list_knowledge_hint_ids_v2(self.rdb, knowledge_key))
            documents.append({
                "example_id": example["example_id"], "knowledge_key": knowledge_key,
                "fingerprint": example.get("fingerprint", ""),
                "exact_reusable": bool(example.get("exact_reusable")),
                "evidence_text": evidence_text,
                "hint_texts": [hints[hint_id] for hint_id in hint_ids if hint_id in hints],
            })
        return documents

    def knowledge(self, knowledge_key: str):
        return get_knowledge_v2(self.rdb, knowledge_key)


class MiniLMEvidenceReranker:
    def __init__(self, backend):
        self.backend = backend

    def rerank(self, query, candidates):
        scores = self.backend.score(query, tuple(item.serialized for item in candidates))
        return tuple(candidate for _, candidate in sorted(
            zip(scores, candidates), key=lambda item: (-item[0], -item[1].bm25_score, item[1].knowledge_key),
        ))
