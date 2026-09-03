"""Redis persistence for completed incident decision traces."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4


class IncidentStore:
    """Save whole incident records so a human can review what the agent used."""

    _index_key = "incident-agent:incidents"

    def __init__(self, redis_client, *, maximum_records: int = 100):
        self._redis = redis_client
        self._maximum_records = maximum_records

    def save(self, record: dict) -> dict:
        """Assign immutable identity metadata before storing the decision trace."""
        saved = {
            **record,
            "id": str(uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._redis.set(self._record_key(saved["id"]), json.dumps(saved, sort_keys=True))
        self._redis.lpush(self._index_key, saved["id"])
        self._redis.ltrim(self._index_key, 0, self._maximum_records - 1)
        return saved

    def get(self, incident_id: str) -> dict | None:
        """Return one stored incident, or ``None`` when its ID is unknown."""
        raw = self._redis.get(self._record_key(incident_id))
        return _decode_json(raw) if raw else None

    def list(self, limit: int = 20) -> list[dict]:
        """Read newest-first IDs, then load their full reviewable records."""
        identifiers = self._redis.lrange(self._index_key, 0, limit - 1)
        records = [self.get(_decode_text(identifier)) for identifier in identifiers]
        return [record for record in records if record is not None]

    @staticmethod
    def _record_key(incident_id: str) -> str:
        return f"incident-agent:incident:{incident_id}"


def _decode_json(raw: str | bytes) -> dict:
    return json.loads(_decode_text(raw))


def _decode_text(raw: str | bytes) -> str:
    return raw.decode("utf-8") if isinstance(raw, bytes) else raw
