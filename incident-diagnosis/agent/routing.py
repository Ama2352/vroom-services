"""Deterministic post-collection routing for evidence and advisory retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evidence import POLICIES, build_evidence_chain, resolve_incident_kind


_FIELDS_BY_SOURCE = {
    "impact.triggering_metric": ("name", "value"),
    "log_evidence": ("message", "operation", "service", "service_name"),
    "trace_handoff": (
        "error_operation", "error_message", "involved_services",
    ),
    "k8s_state": (
        "waiting_reason", "last_terminated_reason", "init_waiting_reason",
        "init_last_terminated_reason", "log_error", "event_reason",
        "event_message", "restarts", "init_restarts",
    ),
    "k8s_event": ("reason", "message"),
    "dependency": ("name", "waiting_reason", "pods_available", "pods_desired"),
    "template_diff": ("env_diff", "old_image", "new_image"),
    "provenance": ("classification", "service", "affected_fields"),
    "dlq_state": ("value", "count"),
}


@dataclass(frozen=True)
class RoutingDecision:
    incident_kind: str
    evidence_chain: dict[str, Any]
    primary_signals: tuple[str, ...]
    secondary_signals: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def to_api_dict(self, include_signals: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "incident_kind": self.incident_kind,
            "primary_signal_count": len(self.primary_signals),
            "secondary_signal_count": len(self.secondary_signals),
            "reason_codes": list(self.reason_codes),
        }
        if include_signals:
            payload["primary_signals"] = list(self.primary_signals)
            payload["secondary_signals"] = list(self.secondary_signals)
        return payload


def _clean(value: Any) -> str:
    return " ".join(str(value).replace("\x00", " ").split())


def _append_signal(signals: list[str], source: str, field: str, value: Any) -> None:
    values = value if isinstance(value, (list, tuple)) else (value,)
    for item in values:
        if item is None or item == "":
            continue
        signal = f"{source}.{field}: {_clean(item)}"
        if signal not in signals:
            signals.append(signal)


def _signals_for_item(item: dict[str, Any]) -> list[str]:
    source = str(item.get("source_path", ""))
    payload = item.get("payload")
    if not isinstance(payload, dict):
        return []

    signals: list[str] = []
    for field in _FIELDS_BY_SOURCE.get(source, ()):
        value = payload.get(field)
        if field == "env_diff" and isinstance(value, list):
            for change in value:
                if not isinstance(change, dict):
                    continue
                for change_field in ("key", "old_value", "new_value"):
                    _append_signal(
                        signals, source,
                        f"env_diff.{change_field}", change.get(change_field),
                    )
            continue
        _append_signal(signals, source, field, value)
    return signals


def _signals_for_roles(chain: dict[str, Any], roles: tuple[str, ...]) -> tuple[str, ...]:
    signals: list[str] = []
    for role in roles:
        for item in chain.get(role, []):
            for signal in _signals_for_item(item):
                if signal not in signals:
                    signals.append(signal)
    return tuple(signals)


def route_incident(alert: dict[str, Any], bundle: dict[str, Any]) -> RoutingDecision:
    explicit = str(alert.get("incident_kind") or "").strip().lower()
    resolved = resolve_incident_kind(alert)

    if resolved not in POLICIES:
        incident_kind = "generic"
        reason_codes = ("generic_fallback",)
    elif explicit:
        incident_kind = resolved
        reason_codes = ("explicit_incident_kind",)
    else:
        incident_kind = resolved
        reason_codes = ("alert_name_fallback",)

    routed_alert = {**alert, "incident_kind": incident_kind}
    chain = build_evidence_chain(routed_alert, bundle)
    primary = _signals_for_roles(chain, ("trigger", "primary"))
    secondary = _signals_for_roles(
        chain, ("causal_context", "consequence", "secondary"),
    )
    return RoutingDecision(
        incident_kind=incident_kind,
        evidence_chain=chain,
        primary_signals=primary,
        secondary_signals=secondary,
        reason_codes=reason_codes,
    )
