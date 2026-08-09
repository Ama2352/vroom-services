"""Publish only a diagnosis that remains safe after evaluation."""


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


def finalize_diagnosis(result: dict, chain: dict, namespace: str, service: str) -> dict:
    """Return publishable fields and discard rejected generated remediation."""
    status = str(result.get("acceptance_status", "accepted"))
    published = not status.startswith("rejected")
    decision = {
        "status": status,
        "published_generated_answer": published,
        "evaluation": dict(result.get("_evaluation") or {}),
    }
    causal_chain_summary = _causal_chain_summary(chain)
    if published:
        return {**result, "diagnosis_decision": decision, "causal_chain_summary": causal_chain_summary}

    symptom = _observed_symptom(chain)
    return {
        "root_cause": f"Insufficient evidence to confirm — observed: {symptom}. Need diagnosis review.",
        "dev_action": "Do not run a remediation command until the diagnosis is reviewed.",
        "kubectl_hint": f"kubectl get pods -n {namespace} -l app={service}",
        "evidence_refs": list(result.get("evidence_refs") or []),
        "acceptance_status": status,
        "low_confidence": True,
        "diagnosis_decision": decision,
        "causal_chain_summary": causal_chain_summary,
        "_step_log": list(result.get("_step_log") or []),
    }
