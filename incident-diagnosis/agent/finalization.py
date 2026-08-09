"""Publish only a diagnosis that remains safe after evaluation."""


def _observed_symptom(chain: dict) -> str:
    for item in chain.get("primary", []):
        message = str((item.get("payload") or {}).get("message", "")).strip()
        if message:
            return message
    return "no decisive primary evidence was collected"


def finalize_diagnosis(result: dict, chain: dict, namespace: str, service: str) -> dict:
    """Return publishable fields and discard rejected generated remediation."""
    status = str(result.get("acceptance_status", "accepted"))
    published = not status.startswith("rejected")
    decision = {
        "status": status,
        "published_generated_answer": published,
        "evaluation": dict(result.get("_evaluation") or {}),
    }
    if published:
        return {**result, "diagnosis_decision": decision}

    symptom = _observed_symptom(chain)
    return {
        "root_cause": f"Insufficient evidence to confirm — observed: {symptom}. Need diagnosis review.",
        "dev_action": "Do not run a remediation command until the diagnosis is reviewed.",
        "kubectl_hint": f"kubectl get pods -n {namespace} -l app={service}",
        "evidence_refs": list(result.get("evidence_refs") or []),
        "acceptance_status": status,
        "low_confidence": True,
        "diagnosis_decision": decision,
        "_step_log": list(result.get("_step_log") or []),
    }
