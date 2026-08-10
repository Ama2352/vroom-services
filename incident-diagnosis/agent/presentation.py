"""Compact operator-facing formatting for a diagnosis and labelled evidence."""

import re


def _text(value, limit=500):
    return str(value).strip()[:limit] if value is not None else ""


def _safe_fallback(service: str, namespace: str) -> tuple[str, str]:
    safe_service = re.sub(r"[^a-z0-9-]", "-", service.lower()).strip("-") or "service"
    safe_namespace = re.sub(r"[^a-z0-9-]", "-", namespace.lower()).strip("-") or "default"
    return (f"Inspect {safe_service} pod health before choosing remediation.",
            f"kubectl get pods -n {safe_namespace} -l app={safe_service}")


def build_presentation(*, alert: dict, diagnosis: dict, diagnosis_confidence: dict,
                       evidence_context: dict, facts: dict, impact: dict,
                       log_evidence: dict, trace_handoff: dict,
                       retrieval_support: dict) -> dict:
    service = _text(alert.get("service")) or "service"
    exact = retrieval_support.get("mode") == "exact_conclusive" and retrieval_support.get("accepted")
    accepted = str(diagnosis.get("acceptance_status", "accepted")).startswith("accepted")
    verdict = "cause_confirmed" if exact or accepted else "review_required"
    log_message = _text(log_evidence.get("message")) or _text(facts.get("log_error"))
    evidence = []
    if log_message:
        evidence.append({"id": "log:selected", "state": "confirmed", "kind": "log", "label": "Structured log", "value": log_message})
    diff = facts.get("configuration_diff") or {}
    if diff.get("status") == "changed":
        changes = diff.get("changes") or []
        value = "; ".join(f"{item.get('path')}: {item.get('previous', 'unset')} → {item.get('current', 'unset')}" for item in changes[:3])
        evidence.append({"id": "config:workload", "state": "context", "kind": "change", "label": "Configuration diff", "value": value or "workload configuration changed"})
    if trace_handoff.get("status") == "correlated":
        evidence.append({"id": f"trace:{_text(trace_handoff.get('trace_id')) or 'selected'}", "state": "confirmed", "kind": "trace", "label": "Correlated trace", "value": _text(trace_handoff.get("error_operation")) or _text(trace_handoff.get("trace_id")), "href": _text(trace_handoff.get("grafana_url")) or None})
    if verdict == "cause_confirmed":
        response = {"mode": "knowledge" if exact else "remediation", "summary": _text(diagnosis.get("dev_action")), "command": _text(diagnosis.get("kubectl_hint")) or None, "expected_result": "Verify the service returns to a healthy state and the observed error stops."}
    else:
        summary, command = _safe_fallback(service, _text(alert.get("namespace")))
        response = {"mode": "investigation", "summary": summary, "command": command, "expected_result": "Collect scoped runtime evidence before selecting remediation."}
    headline = _text(diagnosis.get("root_cause")) or log_message or f"{service} reported an operational failure"
    return {"verdict": verdict, "headline": headline, "summary": headline,
            "confirmed_failure": log_message or f"{service} reported an operational failure",
            "evidence_confidence": _text(diagnosis_confidence.get("level")) or "unknown",
            "answer_source": "knowledge" if exact else "generated" if accepted else "safe_fallback",
            "hypothesis": _text(diagnosis.get("hypothesis")) or None,
            "hypothesis_evidence_refs": list(diagnosis.get("hypothesis_evidence_refs") or []),
            "supporting_evidence": evidence, "recommended_response": response,
            "incident_events": []}


def compact_investigation_response(*, incident_id: str, service: str, namespace: str,
                                   alert_name: str, presentation: dict,
                                   trace_handoff: dict, retrieval_support: dict) -> dict:
    return {"incident_id": incident_id, "alert_name": alert_name, "service": service,
            "namespace": namespace, "verdict": presentation.get("verdict"),
            "headline": presentation.get("headline"),
            "evidence_confidence": presentation.get("evidence_confidence", "unknown"),
            "answer_source": presentation.get("answer_source", "safe_fallback"),
            "recommended_response": presentation.get("recommended_response", {}),
            "trace": {"status": trace_handoff.get("status", "unavailable"), "trace_id": trace_handoff.get("trace_id")},
            "retrieval_support": {"mode": retrieval_support.get("mode", "none"), "accepted": bool(retrieval_support.get("accepted"))}}
