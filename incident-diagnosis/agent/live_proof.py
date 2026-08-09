"""Bounded, sanitized incident evidence used by the read-only proof script."""


def _ids(summary: dict) -> list[str]:
    out: list[str] = []
    for key in ("trigger_ids", "primary_ids", "causal_context_ids", "contradiction_ids"):
        for value in summary.get(key, []) or []:
            if isinstance(value, str) and value not in out:
                out.append(value)
    return out


def build_sanitized_proof(incident: dict) -> dict:
    """Select public diagnosis facts only; never copy debug, credentials, or raw patches."""
    retrieval = incident.get("retrieval_support") or {}
    decision = incident.get("diagnosis_decision") or {}
    summary = incident.get("causal_chain_summary") or {}
    evaluation_steps = [
        {"name": step.get("name"), "duration_ms": step.get("duration_ms", 0), "metadata": step.get("metadata", {})}
        for step in incident.get("timeline", [])
        if step.get("type") == "step" and step.get("name") in {
            "hard_validation", "semantic_critic", "llm_refine",
            "hard_validation_refine", "semantic_critic_refine",
        }
    ]
    return {
        "incident_id": str(incident.get("id", "")),
        "alert_name": str(incident.get("alert_name", "")),
        "service": str(incident.get("service", "")),
        "namespace": str(incident.get("namespace", "")),
        "root_cause": str(incident.get("root_cause", "")),
        "dev_action": str(incident.get("dev_action", "")),
        "kubectl_hint": str(incident.get("kubectl_hint", "")),
        "retrieval_mode": str(retrieval.get("mode", "none")),
        "retrieval_accepted": bool(retrieval.get("accepted", False)),
        "decision": {
            "status": str(decision.get("status", "")),
            "published_generated_answer": bool(decision.get("published_generated_answer", False)),
        },
        "causal_evidence_ids": _ids(summary),
        "evaluation_steps": evaluation_steps,
    }
