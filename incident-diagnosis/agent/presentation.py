"""Pure response shaping for the dashboard and Slack notification layers."""

from __future__ import annotations


def build_incident_response(template, raw_evidence: dict, diagnosis: dict, retrieval: dict) -> dict:
    """Return display data without collecting evidence or making causal decisions."""
    return {
        # Keep every diagnosis field explicit so the UI can show uncertainty.
        "diagnosis": {
            "evidence_analysis": diagnosis.get("evidence_analysis") or {},
            "incident_summary": diagnosis.get("incident_summary", ""),
            "diagnosis_cause": diagnosis.get("diagnosis_cause"),
            "hypothesis": diagnosis.get("hypothesis"),
            "recommended_action": diagnosis.get("recommended_action") or {},
            "used_knowledge_keys": diagnosis.get("used_knowledge_keys") or [],
            "evidence_refs": diagnosis.get("evidence_refs") or [],
            "hypothesis_evidence_refs": diagnosis.get("hypothesis_evidence_refs") or [],
        },
        # Raw evidence remains visible even when the generated answer is downgraded.
        "raw_evidence": raw_evidence,
        "retrieval": retrieval,
        "evidence_template": template.serialize(),
        "evidence_fingerprint": template.fingerprint(),
    }
