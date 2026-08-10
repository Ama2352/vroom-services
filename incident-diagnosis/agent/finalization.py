"""Publish accepted diagnoses or a bounded investigation fallback."""


def finalize_diagnosis(result: dict, context: dict, namespace: str, service: str) -> dict:
    status = str(result.get("acceptance_status", "accepted"))
    if not status.startswith("rejected"):
        return {**result, "diagnosis_decision": {"status": status, "published_generated_answer": True, "published_operator_diagnosis": True}}
    return {
        "root_cause": "Insufficient evidence to confirm a safe root cause. Need diagnosis review.",
        "dev_action": "Do not run a remediation command until the diagnosis is reviewed.",
        "kubectl_hint": f"kubectl get pods -n {namespace} -l app={service}",
        "evidence_refs": [], "acceptance_status": status, "low_confidence": True,
        "diagnosis_decision": {"status": status, "published_generated_answer": False, "published_operator_diagnosis": True},
        "_step_log": list(result.get("_step_log") or []),
    }
