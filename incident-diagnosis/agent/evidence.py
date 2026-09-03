"""Stable evidence representation for retrieval and grounded diagnosis."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


# Fixed order makes the serialized fingerprint deterministic.
TEMPLATE_FIELDS = (
    "alert_name", "service", "triggering_metric", "waiting_reason",
    "last_terminated_reason", "event_reason", "event_message", "log_error",
    "trace_error_service", "trace_error_operation", "trace_error_message",
    "configuration_diff",
)


@dataclass(frozen=True)
class EvidenceTemplate:
    """Stable, answer-free incident representation used by later layers."""

    values: tuple[tuple[str, str], ...]
    # IDs let later diagnosis text cite current observations.
    evidence: tuple[dict[str, Any], ...] = ()

    def serialize(self) -> str:
        return "\n".join(f"{key}: {value}" for key, value in self.values)

    def fingerprint(self) -> str:
        return hashlib.sha256(self.serialize().encode("utf-8")).hexdigest()

    def to_gate_context(self) -> dict[str, Any]:
        return {
            "evidence": list(self.evidence),
            "evidence_ids": sorted(item["id"] for item in self.evidence if item.get("id")),
            "template": self.serialize(),
        }


def normalize_evidence(
    alert: dict[str, Any], facts: dict[str, Any],
    log: dict[str, Any] | None = None,
    trace: dict[str, Any] | None = None,
    configuration: dict[str, Any] | None = None,
) -> EvidenceTemplate:
    """Create a fixed retrieval template while retaining citable observations."""
    log = log or {}
    trace = trace or {}
    values = {
        "alert_name": _template_value(alert.get("alert_name")),
        "service": _template_value(alert.get("service")),
        # The alert name is stable; a numeric reading is not.
        "triggering_metric": _template_value(alert.get("metric_name") or alert.get("alert_name")),
        "waiting_reason": _template_value(facts.get("waiting_reason")),
        "last_terminated_reason": _template_value(facts.get("last_terminated_reason")),
        "event_reason": _template_value(facts.get("event_reason")),
        "event_message": _template_value(facts.get("event_message")),
        "log_error": _template_value(log.get("message") or facts.get("log_error")),
        "trace_error_service": _template_value(trace.get("error_service")),
        "trace_error_operation": _template_value(trace.get("error_operation")),
        "trace_error_message": _template_value(trace.get("error_message")),
        "configuration_diff": _configuration_text(configuration),
    }
    evidence = []
    if values["alert_name"]:
        evidence.append({"id": "alert:trigger", "label": "Alert", "value": values["alert_name"], "state": "confirmed"})
    if values["log_error"]:
        evidence.append({"id": "log:selected", "label": "Structured log", "value": values["log_error"], "state": "confirmed"})
    if any(values[key] for key in ("trace_error_service", "trace_error_operation", "trace_error_message")):
        evidence.append({"id": "trace:selected", "label": "Correlated trace", "value": values["trace_error_operation"], "state": "confirmed"})
    if values["configuration_diff"]:
        evidence.append({"id": "config:workload", "label": "Configuration diff", "value": values["configuration_diff"], "state": "context"})
    if any(values[key] for key in ("waiting_reason", "last_terminated_reason", "event_reason", "event_message")):
        evidence.append({"id": "k8s:state", "label": "Kubernetes", "value": "runtime observation", "state": "confirmed"})
    return EvidenceTemplate(
        values=tuple((field, values[field]) for field in TEMPLATE_FIELDS),
        evidence=tuple(evidence),
    )


def normalize_collected_evidence(alert: dict[str, Any], raw: dict[str, Any]) -> EvidenceTemplate:
    """Adapt collector groups without letting transport-specific shapes leak further."""
    metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
    kubernetes = raw.get("kubernetes") if isinstance(raw.get("kubernetes"), dict) else {}
    facts = {**metrics, **kubernetes}
    log = raw.get("logs") if isinstance(raw.get("logs"), dict) else {}
    trace = raw.get("traces") if isinstance(raw.get("traces"), dict) else {}
    configuration = raw.get("configuration") if isinstance(raw.get("configuration"), dict) else {}

    return normalize_evidence(alert, facts, log, trace, configuration)


# Internal helpers ---------------------------------------------------------

def _clean(value: Any) -> str:
    return " ".join(str(value).replace("\x00", " ").split())


def _template_value(value: Any) -> str:
    cleaned = _clean(value) if value is not None else ""
    # "Unknown" is a Kubernetes placeholder, not evidence.
    return "" if cleaned.lower() == "unknown" else cleaned


def _configuration_text(configuration: dict[str, Any] | None) -> str:
    if not isinstance(configuration, dict) or configuration.get("status") != "changed":
        return ""
    changes = []
    for change in configuration.get("changes", ()):
        if not isinstance(change, dict) or not change.get("path"):
            continue
        changes.append(
            f"{_clean(change['path'])}: {_template_value(change.get('previous'))} -> {_template_value(change.get('current'))}"
        )
    # Stable ordering prevents equivalent diffs from getting different hashes.
    return "; ".join(sorted(set(changes)))
