"""Publish only a diagnosis that remains safe after evaluation."""

import re


def _observed_symptom(chain: dict) -> str:
    for item in chain.get("primary", []):
        message = str((item.get("payload") or {}).get("message", "")).strip()
        if message:
            return message
    return "no decisive primary evidence was collected"


def _causal_chain_summary(chain: dict) -> dict:
    return {
        "incident_kind": str(chain.get("incident_kind", "unknown")),
        "trigger_ids": [str(item.get("id", "")) for item in chain.get("trigger", []) if item.get("id")],
        "primary_ids": [str(item.get("id", "")) for item in chain.get("primary", []) if item.get("id")],
        "causal_context_ids": [str(item.get("id", "")) for item in chain.get("causal_context", []) if item.get("id")],
        "contradiction_ids": [str(item.get("id", "")) for item in chain.get("contradictions", []) if item.get("id")],
    }


def _primary_message(chain: dict) -> str:
    for item in chain.get("primary", []):
        payload = item.get("payload") or {}
        message = str(payload.get("message", "")).strip()
        if message:
            return message
    return ""


def _attribution_status(chain: dict) -> str:
    statuses = []
    classifications = []
    for item in chain.get("causal_context", []):
        payload = item.get("payload") or {}
        status = (payload.get("causal_status") or {}).get("status")
        if status:
            statuses.append(str(status))
        if payload.get("classification"):
            classifications.append(str(payload["classification"]))
    if "conflicting" in statuses:
        return "conflicting"
    if "causal_candidate" in statuses or "hotfix" in classifications:
        return "confirmed"
    if "recent_context" in statuses:
        return "unproven"
    return "unavailable"


def _confirmed_mechanism(chain: dict, service: str) -> str:
    """Return a bounded mechanism only when runtime evidence states the failure plainly."""
    message = _primary_message(chain)
    if not message:
        return ""
    event = re.search(r"unknown event type [\"']([^\"']+)[\"']", message, re.IGNORECASE)
    if event and chain.get("incident_kind") == "dlq":
        return f'{service} rejected event type "{event.group(1)}"; producer-consumer event-contract compatibility is unproven.'
    if chain.get("trigger") and (chain.get("primary") or chain.get("secondary")):
        return message
    return ""


def finalize_diagnosis(result: dict, chain: dict, namespace: str, service: str) -> dict:
    """Return publishable fields and discard rejected generated remediation."""
    status = str(result.get("acceptance_status", "accepted"))
    published = not status.startswith("rejected")
    decision = {
        "status": status,
        "published_generated_answer": published,
        "published_operator_diagnosis": published,
        "evaluation": dict(result.get("_evaluation") or {}),
    }
    causal_chain_summary = _causal_chain_summary(chain)
    if published:
        return {**result, "diagnosis_decision": decision, "causal_chain_summary": causal_chain_summary}

    mechanism = _confirmed_mechanism(chain, service)
    if mechanism:
        return {
            **result,
            "root_cause": mechanism,
            "dev_action": "Do not run a remediation command until the diagnosis is reviewed.",
            "kubectl_hint": f"kubectl get pods -n {namespace} -l app={service}",
            "acceptance_status": "accepted_with_unproven_attribution",
            "failure_status": "confirmed",
            "mechanism_status": "confirmed",
            "attribution_status": _attribution_status(chain),
            "low_confidence": False,
            "diagnosis_decision": {
                **decision,
                "status": "accepted_with_unproven_attribution",
                "failure_status": "confirmed",
                "mechanism_status": "confirmed",
                "attribution_status": _attribution_status(chain),
                "published_operator_diagnosis": True,
            },
            "causal_chain_summary": causal_chain_summary,
            "_step_log": list(result.get("_step_log") or []),
        }

    symptom = _observed_symptom(chain)
    return {
        "root_cause": f"Insufficient evidence to confirm — observed: {symptom}. Need diagnosis review.",
        "dev_action": "Do not run a remediation command until the diagnosis is reviewed.",
        "kubectl_hint": f"kubectl get pods -n {namespace} -l app={service}",
        "evidence_refs": list(result.get("evidence_refs") or []),
        "acceptance_status": status,
        "failure_status": "unconfirmed",
        "mechanism_status": "unconfirmed",
        "attribution_status": _attribution_status(chain),
        "low_confidence": True,
        "diagnosis_decision": {
            **decision,
            "failure_status": "unconfirmed",
            "mechanism_status": "unconfirmed",
            "attribution_status": _attribution_status(chain),
        },
        "causal_chain_summary": causal_chain_summary,
        "_step_log": list(result.get("_step_log") or []),
    }
