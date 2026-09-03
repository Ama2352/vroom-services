"""Grounded generation contract, validation, one refinement, and safe downgrade."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class DiagnosisGate:
    passed: bool
    issues: list[str]


def build_generation_prompt(current_evidence: dict, candidates: list[dict]) -> str:
    """Keep current evidence separate from advisory examples in the LLM prompt."""
    return "\n".join([
        "You are diagnosing one production incident.",
        "CURRENT EVIDENCE - citable:",
        json.dumps(current_evidence, sort_keys=True),
        "NEAREST APPROVED EXAMPLES - advisory, not evidence:",
        json.dumps(candidates[:3], sort_keys=True),
        "Use only current evidence references for confirmed causes and hypotheses.",
        "Return JSON with evidence_analysis, incident_summary, diagnosis_cause, hypothesis, recommended_action, used_knowledge_keys, evidence_refs, hypothesis_evidence_refs.",
        "evidence_analysis must be an object keyed only by evidence groups (alert_metrics, logs, traces, kubernetes, configuration), with short strings as values.",
        "recommended_action must be an object: {\"kind\":\"investigation\"|\"remediation\",\"summary\":\"...\"}.",
    ])


def validate_diagnosis(draft: dict, context: dict) -> DiagnosisGate:
    """Reject malformed output and claims that cite observations we did not collect."""
    known = {item.get("id") for item in context.get("evidence", []) if isinstance(item, dict)}
    issues = []
    if not isinstance(draft.get("evidence_analysis"), dict):
        issues.append("invalid_evidence_analysis")
    for reference in draft.get("evidence_refs") or []:
        if reference not in known:
            issues.append("unknown_evidence_reference")
    hypothesis = draft.get("hypothesis")
    hypothesis_refs = draft.get("hypothesis_evidence_refs") or []
    if hypothesis and not hypothesis_refs:
        issues.append("missing_hypothesis_evidence_reference")
    for reference in hypothesis_refs:
        if reference not in known:
            issues.append("unknown_hypothesis_evidence_reference")
    if draft.get("diagnosis_cause") and not draft.get("evidence_refs"):
        issues.append("missing_evidence_reference")
    if not isinstance(draft.get("incident_summary"), str) or not draft["incident_summary"].strip():
        issues.append("missing_incident_summary")
    action = draft.get("recommended_action")
    if not isinstance(action, dict) or not str(action.get("summary") or "").strip():
        issues.append("missing_recommended_action")
    unique_issues = sorted(set(issues))
    return DiagnosisGate(not unique_issues, unique_issues)


def build_refinement_prompt(original_prompt: str, draft: dict, issues: list[str]) -> str:
    """Give the model one bounded chance to correct known contract violations."""
    return "\n".join([
        original_prompt,
        "Your previous JSON was:", json.dumps(draft, sort_keys=True),
        "Correct only these validation issues:", json.dumps(issues),
        "Return the complete JSON object with the required object shapes.",
    ])


def finalize_diagnosis(draft: dict, context: dict, *, accepted: bool) -> dict:
    """Downgrade unsafe output while preserving usable, cited investigation context."""
    result = dict(draft)
    if accepted:
        return result

    # A failed answer may not publish a confirmed cause or remediation.
    result["diagnosis_cause"] = None
    action = dict(result.get("recommended_action") or {})
    action["kind"] = "investigation"
    action["summary"] = "Collect the cited runtime evidence before selecting remediation."
    result["recommended_action"] = action
    if not isinstance(result.get("evidence_analysis"), dict):
        result["evidence_analysis"] = {}

    valid_ids = set(context.get("evidence_ids") or [])
    hypothesis_ids = set(result.get("hypothesis_evidence_refs") or [])
    if result.get("hypothesis") and not hypothesis_ids.issubset(valid_ids):
        result["hypothesis"] = None
        result["hypothesis_evidence_refs"] = []
    return result
