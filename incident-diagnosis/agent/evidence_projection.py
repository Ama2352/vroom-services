"""Stable, role-free evidence projection for retrieval and diagnosis prompts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


def _clean(value: Any) -> str:
    return " ".join(str(value).replace("\x00", " ").split())


def _add(facts: dict[str, str], key: str, value: Any) -> None:
    if value is None or value == "":
        return
    cleaned = _clean(value)
    if cleaned:
        facts[key] = cleaned


@dataclass(frozen=True)
class EvidenceProjection:
    """A deterministic view of collected facts with no causal role assignment."""

    facts: tuple[tuple[str, str], ...]
    evidence_ids: frozenset[str]

    def lexical_text(self) -> str:
        return "\n".join(f"{key}: {value}" for key, value in self.facts)

    def semantic_text(self) -> str:
        return " ".join(value for _, value in self.facts)

    def to_prompt_dict(self) -> dict[str, str]:
        return dict(self.facts)

    def to_gate_context(self) -> dict[str, Any]:
        """Flat, labelled context shared by the hard validator and critic."""
        return {
            "evidence": [
                {"id": f"fact:{key}", "label": key, "value": value}
                for key, value in self.facts
            ],
            "evidence_ids": sorted(self.evidence_ids),
        }


TEMPLATE_FIELDS = (
    "alert_name", "service", "triggering_metric", "waiting_reason",
    "last_terminated_reason", "event_reason", "event_message", "log_error",
    "trace_error_service", "trace_error_operation", "trace_error_message",
    "configuration_diff",
)


@dataclass(frozen=True)
class EvidenceTemplate:
    """Stable, answer-free incident representation used by retrieval."""

    values: tuple[tuple[str, str], ...]
    evidence: tuple[dict[str, Any], ...] = ()

    def serialize(self) -> str:
        return "\n".join(f"{key}: {value}" for key, value in self.values)

    def fingerprint(self) -> str:
        return hashlib.sha256(self.serialize().encode("utf-8")).hexdigest()

    def to_prompt_groups(self) -> dict[str, dict[str, str]]:
        grouped = {"alert_metrics": {}, "logs": {}, "traces": {}, "kubernetes": {}, "configuration": {}}
        for key, value in self.values:
            if key in {"alert_name", "service", "triggering_metric"}:
                grouped["alert_metrics"][key] = value
            elif key == "log_error":
                grouped["logs"][key] = value
            elif key.startswith("trace_"):
                grouped["traces"][key] = value
            elif key == "configuration_diff":
                grouped["configuration"][key] = value
            else:
                grouped["kubernetes"][key] = value
        return grouped

    def to_gate_context(self) -> dict[str, Any]:
        return {
            "evidence": list(self.evidence),
            "evidence_ids": sorted(item["id"] for item in self.evidence if item.get("id")),
            "template": self.serialize(),
        }


def _template_value(value: Any) -> str:
    return _clean(value) if value is not None else ""


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
    return "; ".join(sorted(set(changes)))


def normalize_evidence(
    alert: dict[str, Any], facts: dict[str, Any], log: dict[str, Any] | None = None,
    trace: dict[str, Any] | None = None, configuration: dict[str, Any] | None = None,
) -> EvidenceTemplate:
    """Create the fixed retrieval template and retain source observations separately."""
    log = log or {}
    trace = trace or {}
    values = {
        "alert_name": _template_value(alert.get("alert_name")),
        "service": _template_value(alert.get("service")),
        # A measured value is volatile and makes identical failures impossible
        # to reuse exactly. The alert identity is the stable triggering signal;
        # the numeric value remains raw operational evidence.
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


def _runtime_facts(facts: dict[str, Any]) -> dict[str, str]:
    projected: dict[str, str] = {}
    for field in (
        "pods_available", "pods_desired", "pods_ready", "pods_running",
        "waiting_reason", "last_terminated_reason", "restarts",
        "init_waiting_reason", "init_last_terminated_reason", "init_restarts",
        "event_reason", "event_message", "log_error",
    ):
        _add(projected, f"runtime.{field}", facts.get(field))
    return projected


def _dependency_facts(dependency: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(dependency, dict):
        return {}
    projected: dict[str, str] = {}
    for field in ("namespace", "name", "pods_available", "pods_desired", "waiting_reason"):
        _add(projected, f"dependency.{field}", dependency.get(field))
    return projected


def _configuration_facts(configuration_diff: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(configuration_diff, dict) or configuration_diff.get("status") != "changed":
        return {}
    projected: dict[str, str] = {}
    for change in configuration_diff.get("changes", ()):
        if not isinstance(change, dict) or not change.get("path"):
            continue
        path = _clean(change["path"])
        _add(projected, f"config.{path}.previous", change.get("previous"))
        _add(projected, f"config.{path}.current", change.get("current"))
    return projected


def build_evidence_projection(
    alert: dict[str, Any] | str,
    facts: dict[str, Any],
    log_evidence: dict[str, Any] | None = None,
    trace_handoff: dict[str, Any] | None = None,
    dependency: dict[str, Any] | None = None,
    configuration_diff: dict[str, Any] | None = None,
) -> EvidenceProjection:
    projected: dict[str, str] = {}
    evidence_ids: set[str] = set()

    if isinstance(alert, str):
        alert = {"alert_name": alert}
    _add(projected, "alert.name", alert.get("alert_name"))
    _add(projected, "alert.value", alert.get("metric_value"))
    _add(projected, "alert.threshold", alert.get("threshold"))
    _add(projected, "alert.window", alert.get("window"))
    if alert.get("alert_name"):
        evidence_ids.add(f"alert:{_clean(alert['alert_name'])}")

    runtime = _runtime_facts(facts)
    projected.update(runtime)
    for key in runtime:
        evidence_ids.add(f"runtime:{key.split('.', 1)[1]}")

    if isinstance(log_evidence, dict) and log_evidence.get("status") == "found":
        _add(projected, "log.message", log_evidence.get("message") or facts.get("log_error"))
        _add(projected, "log.operation", log_evidence.get("operation"))
        _add(projected, "log.service", log_evidence.get("service"))
        event_id = _clean(log_evidence.get("event_id") or "selected")
        evidence_ids.add(f"log:{event_id}")

    if isinstance(trace_handoff, dict) and trace_handoff.get("status") == "correlated":
        _add(projected, "trace.id", trace_handoff.get("trace_id"))
        _add(projected, "trace.operation", trace_handoff.get("error_operation"))
        evidence_ids.add(f"trace:{_clean(trace_handoff.get('trace_id') or 'selected')}")

    dependencies = _dependency_facts(dependency)
    projected.update(dependencies)
    if dependencies.get("dependency.name"):
        evidence_ids.add(f"dependency:{dependencies['dependency.name']}")

    configuration = _configuration_facts(configuration_diff)
    projected.update(configuration)
    if configuration:
        evidence_ids.add("config:workload")

    return EvidenceProjection(
        facts=tuple(sorted(projected.items())),
        evidence_ids=frozenset(evidence_ids),
    )
