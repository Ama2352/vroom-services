from datetime import datetime, timedelta, timezone
from typing import Any


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def incident_window(starts_at: str, now: datetime) -> tuple[int, int]:
    """Return a five-minute pre-alert window bounded to fifteen minutes after alert start."""
    started = _parse_timestamp(starts_at)
    current = now.astimezone(timezone.utc)
    end = min(current, started + timedelta(minutes=15))
    return int((started - timedelta(minutes=5)).timestamp()), int(end.timestamp())


def normalize_alert(data: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "fingerprint": data.get("fingerprint", ""),
        "starts_at": data.get("starts_at", ""),
        "alert_name": data.get("alert_name", "UnknownAlert"),
        "incident_kind": data.get("incident_kind") or "",
        "service": data.get("service") or "unknown",
        "namespace": data.get("namespace") or "unknown",
        "pod": data.get("pod", ""),
        "severity": data.get("severity", "warning"),
        "status": data.get("status", "firing"),
    }
    for field in ("metric_value", "threshold"):
        value = data.get(field)
        try:
            normalized[field] = float(value) if value is not None else None
        except (TypeError, ValueError):
            normalized[field] = None
    try:
        _parse_timestamp(normalized["starts_at"])
    except (TypeError, ValueError):
        normalized["starts_at_error"] = "invalid Alertmanager starts_at timestamp"
    return normalized
