"""Decision layer for the evidence-first investigation contract.

Retrieval chooses approved examples; it never becomes incident evidence.  Exact
examples reuse human-approved knowledge.  All other results are advisory and
must retain a grounded hypothesis plus the nearest three examples.
"""

from __future__ import annotations

from diagnosis_v2 import build_generation_prompt, finalize_diagnosis_v2, validate_diagnosis_v2
from retrieval.evidence import EvidenceRetrievalMode


def _current_context(template):
    return template.to_gate_context()


def _fallback(template) -> dict:
    context = _current_context(template)
    refs = context["evidence_ids"]
    log = next((item["value"] for item in context["evidence"] if item["id"] == "log:selected"), "")
    return {
        "evidence_analysis": {},
        "incident_summary": log or "The alert fired for the service.",
        "diagnosis_cause": None,
        "hypothesis": ("The observed structured error may explain the alert." if log else None),
        "recommended_action": {"kind": "investigation", "summary": "Review the cited current evidence before selecting remediation."},
        "used_knowledge_keys": [],
        "evidence_refs": refs,
        "hypothesis_evidence_refs": ["log:selected"] if log else [],
    }


def _advisory_examples(retrieval) -> list[dict]:
    seen, examples = set(), []
    for candidate in retrieval.candidates:
        if candidate.knowledge_key in seen:
            continue
        seen.add(candidate.knowledge_key)
        examples.append({
            "knowledge_key": candidate.knowledge_key,
            "example_id": candidate.example_id,
            "evidence_template": candidate.serialized,
        })
        if len(examples) == 3:
            break
    return examples


def decide_diagnosis(template, retrieval, generate, *, knowledge: dict | None = None) -> dict:
    """Return only v2 diagnosis fields, never legacy root-cause presentation keys."""
    context = _current_context(template)
    if retrieval.mode is EvidenceRetrievalMode.EXACT and knowledge:
        return {
            "evidence_analysis": {},
            "incident_summary": "Current evidence matches an approved incident example.",
            "diagnosis_cause": knowledge.get("diagnosis_cause") or None,
            "hypothesis": None,
            "recommended_action": {"kind": "remediation", "summary": knowledge.get("remediation") or "Review the approved remediation."},
            "used_knowledge_keys": [retrieval.candidates[0].knowledge_key],
            "evidence_refs": context["evidence_ids"],
            "hypothesis_evidence_refs": [],
            "advisory_examples": [],
        }

    advisory = _advisory_examples(retrieval)
    try:
        draft = generate(build_generation_prompt(context, advisory))
    except Exception:
        draft = None
    if not isinstance(draft, dict):
        draft = _fallback(template)
    gate = validate_diagnosis_v2(draft, context)
    result = finalize_diagnosis_v2(draft, context, accepted=gate.passed)
    result["advisory_examples"] = advisory
    return result
