"""Redis persistence for human-approved retrieval knowledge."""

from __future__ import annotations

import json

from evidence import TEMPLATE_FIELDS


_EMPTY_KNOWLEDGE = {"families": [], "examples": [], "hints": []}


class KnowledgeStore:
    """Keep approved examples separate from live incident records."""

    _key = "incident-agent:knowledge"

    def __init__(self, redis_client):
        self._redis = redis_client

    def load(self) -> dict:
        """Return the predictable empty structure before knowledge is curated."""
        raw = self._redis.get(self._key)
        if not raw:
            return {name: list(items) for name, items in _EMPTY_KNOWLEDGE.items()}
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        return json.loads(text)

    def save(self, knowledge: dict) -> None:
        """Store the reviewable knowledge document as one stable JSON value."""
        self._redis.set(self._key, json.dumps(knowledge, sort_keys=True))

    def corpus(self) -> "KnowledgeCorpus":
        """Freeze one read view so retrieval and exact lookup use the same approval set."""
        return KnowledgeCorpus(self.load())


class KnowledgeCorpus:
    """Adapt the stored knowledge document to the retrieval service's small contract."""

    def __init__(self, knowledge: dict):
        self._knowledge = knowledge

    def get_documents(self) -> list[dict]:
        """Return evidence and hints only; advisory retrieval must not see answer text."""
        hints = {
            item.get("hint_id"): item.get("text", "")
            for item in self._knowledge.get("hints", [])
            if isinstance(item, dict) and item.get("hint_id")
        }
        documents = []
        for example in self._knowledge.get("examples", []):
            if not isinstance(example, dict) or not example.get("example_id") or not example.get("knowledge_key"):
                continue
            documents.append({
                "example_id": str(example["example_id"]),
                "knowledge_key": str(example["knowledge_key"]),
                "fingerprint": str(example.get("fingerprint", "")),
                "exact_reusable": bool(example.get("exact_reusable")),
                "evidence_text": _evidence_text(example.get("evidence")),
                "hint_texts": [hints[hint_id] for hint_id in example.get("hint_ids", []) if hint_id in hints],
            })
        return documents

    def knowledge(self, knowledge_key: str) -> dict | None:
        """Return the approved family only for a verified exact evidence match."""
        return next(
            (
                family for family in self._knowledge.get("families", [])
                if isinstance(family, dict) and family.get("knowledge_key") == knowledge_key
            ),
            None,
        )


def _evidence_text(evidence: object) -> str:
    if isinstance(evidence, str):
        return evidence
    if not isinstance(evidence, dict):
        return ""
    return "\n".join(f"{field}: {evidence.get(field, '')}" for field in TEMPLATE_FIELDS)
