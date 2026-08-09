"""Declarative, alert-relative evidence ordering for incident diagnosis."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvidencePolicy:
    trigger: tuple[str, ...]
    primary: tuple[str, ...]
    causal_context: tuple[str, ...]
    consequence: tuple[str, ...] = ()
    secondary: tuple[str, ...] = ()
    required: tuple[str, ...] = ()


POLICIES = {
    "dlq": EvidencePolicy(
        trigger=("impact.triggering_metric",),
        primary=("log_evidence", "trace_handoff"),
        causal_context=("template_diff", "provenance"),
        consequence=("dlq_state",),
        secondary=("k8s_event", "k8s_state", "dependency"),
        required=("impact.triggering_metric", "log_evidence"),
    ),
    "crashloop": EvidencePolicy(
        trigger=("k8s_state",),
        primary=("k8s_state", "log_evidence"),
        causal_context=("template_diff", "provenance"),
        secondary=("impact", "trace_handoff", "k8s_event"),
        required=("k8s_state",),
    ),
}

_ALERT_KIND_MAP = {
    "DLQEventsDetected": "dlq",
    "DLQEventsDetectedHigh": "dlq",
    "KubePodCrashLooping": "crashloop",
}


def resolve_incident_kind(alert: dict[str, Any]) -> str:
    explicit = str(alert.get("incident_kind") or "").strip().lower()
    if explicit:
        return explicit
    return _ALERT_KIND_MAP.get(alert.get("alert_name", ""), "generic")


def _get_path(bundle: dict[str, Any], path: str) -> Any:
    value: Any = bundle
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _present(value: Any) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return False
    if isinstance(value, dict):
        if value.get("status") in {
            "no_data", "no_match", "no_trace_id", "not_found", "unavailable", "invalid",
        }:
            return False
        meaningful = [item for key, item in value.items() if key != "status"]
        if meaningful and not any(item not in (None, "", [], {}) for item in meaningful):
            return False
    return True


def _item_id(path: str, value: Any) -> str:
    if path == "impact.triggering_metric":
        name = value.get("name", "metric") if isinstance(value, dict) else str(value)
        return f"metric:{name}"
    if path == "log_evidence":
        event_id = value.get("event_id") if isinstance(value, dict) else None
        return f"log:{event_id or 'selected'}"
    if path == "trace_handoff":
        trace_id = value.get("trace_id") if isinstance(value, dict) else None
        return f"trace:{trace_id or 'selected'}"
    if path == "k8s_event":
        event_id = value.get("id") if isinstance(value, dict) else None
        return f"k8s:event:{event_id or 'latest'}"
    if path == "template_diff":
        return "change:template"
    if path == "provenance":
        commit = value.get("commit") if isinstance(value, dict) else None
        sha = commit.get("sha") if isinstance(commit, dict) else None
        return f"change:{sha or 'provenance'}"
    return path.replace(".", ":")


def _role_items(policy: EvidencePolicy, role: str, bundle: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for path in getattr(policy, role):
        value = _get_path(bundle, path)
        if not _present(value):
            continue
        items.append({
            "id": _item_id(path, value),
            "role": role,
            "status": value.get("status", "available") if isinstance(value, dict) else "available",
            "payload": value,
            "reason": f"incident policy {role} selected {path}",
            "source_path": path,
        })
    return items


def build_evidence_chain(alert: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    kind = resolve_incident_kind(alert)
    policy = POLICIES.get(kind, EvidencePolicy(
        trigger=("impact",), primary=("log_evidence",), causal_context=("provenance",),
        secondary=("k8s_state", "k8s_event", "dependency", "trace_handoff"),
    ))
    chain = {"incident_kind": kind, "policy": {
                "trigger": list(policy.trigger), "primary": list(policy.primary),
                "causal_context": list(policy.causal_context), "consequence": list(policy.consequence),
                "secondary": list(policy.secondary), "required": list(policy.required),
             }, "trigger": [], "primary": [],
             "causal_context": [], "consequence": [], "secondary": [], "required": list(policy.required)}
    for role in ("trigger", "primary", "causal_context", "consequence", "secondary"):
        chain[role] = _role_items(policy, role, bundle)
    return chain
