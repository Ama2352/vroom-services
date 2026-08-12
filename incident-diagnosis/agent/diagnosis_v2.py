from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class DiagnosisGate:
    passed: bool
    issues: list[str]


def build_generation_prompt(current_evidence: dict, candidates: list[dict]) -> str:
    return "\n".join([
        "You are diagnosing one production incident.",
        "CURRENT EVIDENCE - citable:",
        json.dumps(current_evidence, sort_keys=True),
        "NEAREST APPROVED EXAMPLES - advisory, not evidence:",
        json.dumps(candidates[:3], sort_keys=True),
        "Use only current evidence references for confirmed causes and hypotheses.",
        "Return JSON with evidence_analysis, incident_summary, diagnosis_cause, hypothesis, recommended_action, used_knowledge_keys, evidence_refs, hypothesis_evidence_refs.",
    ])


def validate_diagnosis_v2(draft: dict, context: dict) -> DiagnosisGate:
    known = {item.get("id") for item in context.get("evidence", []) if isinstance(item, dict)}
    issues = []
    for ref in draft.get("evidence_refs") or []:
        if ref not in known:
            issues.append("unknown_evidence_reference")
    hypothesis = draft.get("hypothesis")
    refs = draft.get("hypothesis_evidence_refs") or []
    if hypothesis and not refs:
        issues.append("missing_hypothesis_evidence_reference")
    for ref in refs:
        if ref not in known:
            issues.append("unknown_hypothesis_evidence_reference")
    if draft.get("diagnosis_cause") and not draft.get("evidence_refs"):
        issues.append("missing_evidence_reference")
    if not isinstance(draft.get("incident_summary"), str) or not draft["incident_summary"].strip():
        issues.append("missing_incident_summary")
    action = draft.get("recommended_action")
    if not isinstance(action, dict) or not str(action.get("summary") or "").strip():
        issues.append("missing_recommended_action")
    unique = sorted(set(issues))
    return DiagnosisGate(not unique, unique)


def finalize_diagnosis_v2(draft: dict, context: dict, *, accepted: bool) -> dict:
    result = dict(draft)
    if accepted:
        return result
    result["diagnosis_cause"] = None
    raw_action = result.get("recommended_action")
    action = dict(raw_action) if isinstance(raw_action, dict) else {}
    action["kind"] = "investigation"
    action["summary"] = "Collect the cited runtime evidence before selecting remediation."
    result["recommended_action"] = action
    refs = set(context.get("evidence_ids") or {
        item.get("id") for item in context.get("evidence", []) if isinstance(item, dict)
    })
    hypothesis_refs = set(result.get("hypothesis_evidence_refs") or [])
    if result.get("hypothesis") and not hypothesis_refs.issubset(refs):
        result["hypothesis"] = None
        result["hypothesis_evidence_refs"] = []
    return result
