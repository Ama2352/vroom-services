def assess_confidence(alert: dict, impact: dict, log: dict, trace: dict, facts: dict) -> dict:
    reasons = []
    missing = []
    if trace.get("status") == "conflict":
        return {"level": "unknown", "reasons": [], "missing_evidence": ["trace evidence conflicts with the selected log"]}
    if impact.get("status") != "available":
        missing.append("triggering impact metrics are unavailable")
    else:
        reasons.append("Prometheus measured the triggering service impact")
    if log.get("status") != "found":
        missing.append("no scoped structured error log was found")
    else:
        reasons.append("a scoped structured error log identifies the failing operation")
    if trace.get("status") == "correlated":
        reasons.append("the exact log trace ID resolves to an agreeing Tempo error span")
    elif trace.get("status") == "no_trace_id":
        missing.append("the selected log has no valid trace ID")
    else:
        missing.append("an agreeing Tempo trace is unavailable")
    specific_fact = any(facts.get(key) for key in ("kubernetes", "changes", "dependencies"))
    if specific_fact:
        reasons.append("additional Kubernetes, change, or dependency evidence is available")
    if impact.get("status") == "available" and log.get("status") == "found" and trace.get("status") == "correlated":
        return {"level": "high", "reasons": reasons, "missing_evidence": missing}
    if impact.get("status") == "available" and log.get("status") == "found":
        return {"level": "medium" if reasons else "low", "reasons": reasons, "missing_evidence": missing}
    if reasons:
        return {"level": "low", "reasons": reasons, "missing_evidence": missing}
    return {"level": "unknown", "reasons": reasons, "missing_evidence": missing}


def align_root_cause_confidence(root_cause: str, confidence: dict) -> str:
    """Never turn evidence completeness into causal certainty.

    Metric, log, and trace agreement can confirm a failure observation, but only
    the diagnosis gates can accept a root-cause claim. Preserve the generator's
    explicit uncertainty instead of relabeling the observation as the cause.
    """
    return root_cause
