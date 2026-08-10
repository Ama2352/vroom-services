"""Bounded, operator-facing incident presentation and webhook response builders."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Literal


EvidenceState = Literal["confirmed", "context", "missing", "conflicting"]
Verdict = Literal["cause_confirmed", "review_required", "evaluation_unavailable"]

_MAX_VALUE = 240
_MAX_DETAIL = 1000
_PLACEHOLDER = re.compile(r"<[^>]+>")
_EVENT_TYPE = re.compile(r"unknown event type [\"']([^\"']+)[\"']", re.IGNORECASE)


def _text(value: Any, limit: int = _MAX_VALUE) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:limit]


def _without_placeholder(value: Any) -> str:
    text = _text(value)
    return "" if _PLACEHOLDER.search(text) else text


def _status(decision: dict) -> str:
    return _text(decision.get("status"))


def _causal_context(evidence_chain: dict) -> list[dict]:
    return [item for item in evidence_chain.get("causal_context", []) if isinstance(item, dict)]


def _causal_state(evidence_chain: dict) -> tuple[str, str]:
    contexts = _causal_context(evidence_chain)
    statuses: list[str] = []
    classifications: list[str] = []
    for item in contexts:
        payload = item.get("payload") or {}
        status = (payload.get("causal_status") or {}).get("status")
        if status:
            statuses.append(_text(status))
        if payload.get("classification"):
            classifications.append(_text(payload.get("classification")))
    if "conflicting" in statuses:
        return "conflicting", "provenance conflicts with the observed failure"
    if "causal_candidate" in statuses:
        return "confirmed", "provenance and the observed failure share a causal relationship"
    if "hotfix" in classifications:
        return "confirmed", "the runtime configuration change matches the observed failure"
    if "recent_context" in statuses:
        return "context", "a recent change exists, but its relationship to the failure is unproven"
    return "missing", "no related change or causal context was established"


def _log_message(log_evidence: dict, facts: dict) -> str:
    message = _text(log_evidence.get("message"))
    if message:
        return message
    return _text(facts.get("log_error"))


def _failure_statement(service: str, log_message: str, incident_kind: str) -> str:
    match = _EVENT_TYPE.search(log_message)
    if match:
        return f'{service} rejected event type "{match.group(1)}"'
    if log_message:
        return log_message
    if incident_kind == "dlq":
        return f"{service} has a dead-letter queue event"
    return f"{service} reported an operational failure"


def _add_item(items: list[dict], *, item_id: str, state: EvidenceState, kind: str,
              label: str, value: Any, detail: Any = "", occurred_at: Any = None,
              href: Any = None) -> None:
    bounded = _text(value)
    if not bounded:
        return
    items.append({
        "id": _text(item_id), "state": state, "kind": _text(kind),
        "label": _text(label), "value": bounded, "detail": _text(detail, _MAX_DETAIL),
        "occurred_at": occurred_at if isinstance(occurred_at, str) else None,
        "href": _text(href) or None,
    })


def _change_value(facts: dict) -> tuple[str, str]:
    diff = facts.get("template_diff") or {}
    env_diff = diff.get("env_diff") or []
    if env_diff:
        change = env_diff[0]
        return (
            f"{_text(change.get('key'))}: {_text(change.get('old_value'))} → {_text(change.get('new_value'))}",
            _text(diff.get("changed_at")),
        )
    if diff.get("image_changed"):
        return f"image: {_text(diff.get('old_image'))} → {_text(diff.get('new_image'))}", _text(diff.get("changed_at"))
    return "", ""


def _safe_fallback(service: str, namespace: str) -> tuple[str, str]:
    safe_service = re.sub(r"[^a-z0-9-]", "-", _text(service).lower()).strip("-") or "service"
    safe_namespace = re.sub(r"[^a-z0-9-]", "-", _text(namespace).lower()).strip("-") or "default"
    return (
        f"Inspect {safe_service} pod health before choosing remediation.",
        f"kubectl get pods -n {safe_namespace} -l app={safe_service}",
    )


def _metric_display(payload: dict, incident_kind: str = "") -> tuple[str, str, str]:
    name = "DLQ events" if incident_kind == "dlq" else _text(payload.get("name")) or "Impact metric"
    value = payload.get("value")
    if isinstance(value, (int, float)) and incident_kind == "dlq":
        count = int(round(value))
        value_text = f"{count} event" + ("" if count == 1 else "s") + " in the last 5 min"
    elif isinstance(value, (int, float)):
        value_text = f"{value:g}"
    else:
        value_text = _text(value)
    threshold = payload.get("threshold")
    detail = f"Alert threshold: > {threshold:g} event" if isinstance(threshold, (int, float)) and incident_kind == "dlq" else ""
    return name, value_text, detail


def _incident_events(alert: dict, evidence_chain: dict, facts: dict, log_message: str,
                     verdict: Verdict, causal_state: str) -> list[dict]:
    events: list[dict] = []
    started_at = _text(alert.get("starts_at")) or None
    change_value, changed_at = _change_value(facts)
    if change_value:
        events.append({"id": "event:change", "occurred_at": changed_at or started_at,
                       "state": "confirmed" if causal_state == "confirmed" else "context",
                       "title": "Runtime configuration changed" if "env_diff" in (facts.get("template_diff") or {}) else "Recent deployment reviewed",
                       "summary": change_value, "evidence_ids": ["change:template"]})
    if log_message:
        events.append({"id": "event:failure", "occurred_at": started_at, "state": "confirmed",
                       "title": "Failure observed", "summary": _text(log_message), "evidence_ids": ["log:selected"]})
    if facts.get("pods_ready") is not None and facts.get("pods_desired"):
        events.append({"id": "event:impact", "occurred_at": started_at, "state": "confirmed",
                       "title": "Service impact detected", "summary": f"Readiness: {facts.get('pods_ready')}/{facts.get('pods_desired')} ready",
                       "evidence_ids": ["k8s_state"]})
    if started_at:
        events.append({"id": "event:alert", "occurred_at": started_at, "state": "confirmed",
                       "title": "Alert fired", "summary": _text(alert.get("alert_name")), "evidence_ids": []})
    events.append({
        "id": "event:diagnosis", "occurred_at": started_at,
        "state": "confirmed" if verdict == "cause_confirmed" else "review",
        "title": "Diagnosis published" if verdict == "cause_confirmed" else "Causal attribution withheld",
        "summary": "Causal link accepted" if verdict == "cause_confirmed" else "Failure retained; unsupported cause withheld",
        "evidence_ids": [item.get("id") for item in evidence_chain.get("primary", []) if item.get("id")],
    })
    return events


def build_presentation(*, alert: dict, diagnosis: dict, diagnosis_confidence: dict,
                       evidence_chain: dict, facts: dict, impact: dict,
                       log_evidence: dict, trace_handoff: dict,
                       retrieval_support: dict) -> dict:
    decision = diagnosis.get("diagnosis_decision") or {}
    decision_status = _status(decision)
    published = decision.get("published_generated_answer") is True
    operator_published = decision.get("published_operator_diagnosis") is True
    retrieval_mode = _text(retrieval_support.get("mode"))
    exact_knowledge = retrieval_mode == "exact_conclusive" and retrieval_support.get("accepted") is True
    causal_state, causal_reason = _causal_state(evidence_chain)
    has_decision = bool(decision)
    if exact_knowledge or (published and causal_state == "confirmed"):
        verdict: Verdict = "cause_confirmed"
    elif not has_decision:
        verdict = "evaluation_unavailable"
    else:
        verdict = "review_required"

    incident_kind = _text(evidence_chain.get("incident_kind"), 80) or "generic"
    service = _text(alert.get("service") or facts.get("service")) or "service"
    log_message = _log_message(log_evidence, facts)
    trigger = (evidence_chain.get("trigger") or [])[:1]
    primary = (evidence_chain.get("primary") or [])[:2]
    confirmed_failure = _failure_statement(service, log_message, incident_kind)
    failure_status = _text(diagnosis.get("failure_status")) or ("confirmed" if log_message else "unconfirmed")
    mechanism_status = _text(diagnosis.get("mechanism_status")) or ("confirmed" if log_message and (trigger or primary) else "unconfirmed")
    attribution_status = _text(diagnosis.get("attribution_status")) or ("confirmed" if causal_state == "confirmed" else "unproven" if causal_state == "context" else "unavailable")
    root_cause = _without_placeholder(diagnosis.get("root_cause"))
    headline = root_cause if verdict == "cause_confirmed" and root_cause else confirmed_failure
    if verdict == "review_required":
        headline = f"{confirmed_failure}; change attribution not established" if mechanism_status == "confirmed" else f"{confirmed_failure}; cause not established"
    if verdict == "evaluation_unavailable":
        headline = confirmed_failure

    evidence: list[dict] = []
    contexts = _causal_context(evidence_chain)[:1]
    for item in trigger:
        payload = item.get("payload") or {}
        metric_name, metric_value, metric_detail = _metric_display(payload, incident_kind)
        _add_item(evidence, item_id=item.get("id", "metric:impact"), state="confirmed", kind="metric",
                  label=metric_name, value=metric_value,
                  detail=metric_detail or item.get("reason"))
    for item in primary:
        payload = item.get("payload") or {}
        if str(item.get("id", "")).startswith("trace:"):
            continue
        _add_item(evidence, item_id=item.get("id", "log:selected"), state="confirmed", kind="log",
                  label="Structured log" if payload.get("log_format") != "plain" else "Runtime log",
                  value=payload.get("message") or log_message, detail=item.get("reason"))
    if trace_handoff.get("status") == "correlated":
        _add_item(evidence, item_id=f"trace:{_text(trace_handoff.get('trace_id')) or 'selected'}", state="confirmed", kind="trace",
                  label="Correlated trace", value=trace_handoff.get("error_operation") or trace_handoff.get("trace_id"),
                  detail=trace_handoff.get("error_message"), href=trace_handoff.get("grafana_url"))
    for item in contexts:
        payload = item.get("payload") or {}
        item_state: EvidenceState = "confirmed" if causal_state == "confirmed" else causal_state  # type: ignore[assignment]
        change_value, changed_at = _change_value(facts)
        provenance = facts.get("provenance") or {}
        dual = provenance.get("dual") or {}
        source = dual.get("service_source") or {}
        source_note = ""
        if source.get("source_relevance") == "no_relevant_service_files":
            source_note = f"No relevant {provenance.get('service', service)} source files changed in this revision."
        _add_item(evidence, item_id=item.get("id", "change:provenance"), state=item_state, kind="change",
                  label="Related change" if item_state == "confirmed" else "Recent change",
                  value=change_value or payload.get("classification") or "change context available",
                  detail=source_note or causal_reason, occurred_at=changed_at)

    answer_source = "knowledge" if exact_knowledge else "generated" if published else "safe_fallback"
    if verdict == "cause_confirmed":
        response_mode = "knowledge" if exact_knowledge else "remediation"
        response_summary = _without_placeholder(diagnosis.get("dev_action")) or "Apply the validated remediation."
        command = _without_placeholder(diagnosis.get("kubectl_hint")) or None
        rationale = causal_reason
        expected = "Verify the service returns to a healthy state and the observed error stops."
    else:
        response_mode = "investigation"
        response_summary, command = _safe_fallback(service, _text(alert.get("namespace")) or "default")
        rationale = "The observed failure is retained, but the available evidence does not establish a safe causal remediation."
        expected = "Collect the scoped runtime evidence before selecting a remediation."

    causal_basis = causal_reason if verdict == "cause_confirmed" else None
    evidence_gap = None if verdict == "cause_confirmed" else (
        "The failure and mechanism are confirmed; change attribution is not proven causal."
        if mechanism_status == "confirmed" and attribution_status != "confirmed" else causal_reason
    )
    return {
        "verdict": verdict,
        "headline": _text(headline),
        "summary": _text(root_cause if verdict != "cause_confirmed" else causal_reason),
        "confirmed_failure": _text(confirmed_failure),
        "failure_status": failure_status,
        "mechanism_status": mechanism_status,
        "attribution_status": attribution_status,
        "causal_basis": _text(causal_basis) or None,
        "evidence_gap": _text(evidence_gap) or None,
        "evidence_confidence": _text(diagnosis_confidence.get("level")) or "unknown",
        "answer_source": answer_source,
        "supporting_evidence": evidence[:4],
        "recommended_response": {
            "mode": response_mode, "summary": _text(response_summary),
            "rationale": _text(rationale), "command": command,
            "expected_result": _text(expected),
        },
        "incident_events": _incident_events(alert, evidence_chain, facts, log_message, verdict, causal_state),
    }


def compact_investigation_response(*, incident_id: str, service: str, namespace: str,
                                   alert_name: str, presentation: dict,
                                   trace_handoff: dict, retrieval_support: dict) -> dict:
    trace = {"status": _text(trace_handoff.get("status")) or "unavailable"}
    for key in ("trace_id", "error_service", "error_operation", "grafana_url"):
        if trace_handoff.get(key):
            trace[key] = _text(trace_handoff[key])
    return {
        "incident_id": _text(incident_id), "alert_name": _text(alert_name),
        "service": _text(service), "namespace": _text(namespace),
        "verdict": _text(presentation.get("verdict")),
        "headline": _text(presentation.get("headline")),
        "evidence_confidence": _text(presentation.get("evidence_confidence")) or "unknown",
        "answer_source": _text(presentation.get("answer_source")) or "safe_fallback",
        "recommended_response": presentation.get("recommended_response") or {},
        "trace": trace,
        "retrieval_support": {
            "mode": _text(retrieval_support.get("mode")) or "none",
            "accepted": bool(retrieval_support.get("accepted")),
        },
    }
