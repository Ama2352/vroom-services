"""Decision layer: exact reuse or grounded, advisory diagnosis."""

from __future__ import annotations

from critic import run_semantic_critic
from diagnosis import (
    build_generation_prompt,
    build_refinement_prompt,
    finalize_diagnosis,
    validate_diagnosis,
)
from retrieval.models import EvidenceRetrievalMode


def _fallback(template) -> dict:
    """A provider failure must not hide the incident evidence from the operator."""
    context = template.to_gate_context()
    log = next((item["value"] for item in context["evidence"] if item["id"] == "log:selected"), "")
    return {
        "evidence_analysis": {},
        "incident_summary": log or "The alert fired for the service.",
        "diagnosis_cause": None,
        "hypothesis": "The observed structured error may explain the alert." if log else None,
        "recommended_action": {
            "kind": "investigation",
            "summary": "Review the cited current evidence before selecting remediation.",
        },
        "used_knowledge_keys": [],
        "evidence_refs": context["evidence_ids"],
        "hypothesis_evidence_refs": ["log:selected"] if log else [],
    }


def _advisory_examples(retrieval) -> list[dict]:
    """Expose at most three distinct examples as guidance, never as live proof."""
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


def _validate_with_critic(draft: dict, context: dict, generate) -> tuple[bool, list[str]]:
    """Both shape/citation checks and evidence-grounding review must pass."""
    gate = validate_diagnosis(draft, context)
    if not gate.passed:
        return False, gate.issues

    critic = run_semantic_critic(context, draft, generate=generate)
    return critic.passed, critic.issues


def decide_diagnosis(template, retrieval, generate, *, knowledge: dict | None = None) -> dict:
    """Return an approved exact diagnosis or a guarded non-exact hypothesis."""
    context = template.to_gate_context()
    if retrieval.mode is EvidenceRetrievalMode.EXACT and knowledge:
        # A human previously approved this identical evidence-to-knowledge mapping.
        return {
            "evidence_analysis": {},
            "incident_summary": "Current evidence matches an approved incident example.",
            "diagnosis_cause": knowledge.get("diagnosis_cause") or None,
            "hypothesis": None,
            "recommended_action": {
                "kind": "remediation",
                "summary": knowledge.get("remediation") or "Review the approved remediation.",
            },
            "used_knowledge_keys": [retrieval.candidates[0].knowledge_key],
            "evidence_refs": context["evidence_ids"],
            "hypothesis_evidence_refs": [],
            "advisory_examples": [],
        }

    advisory = _advisory_examples(retrieval)
    prompt = build_generation_prompt(context, advisory)
    try:
        draft = generate(prompt)
    except Exception:
        draft = None
    if not isinstance(draft, dict):
        draft = _fallback(template)

    accepted, issues = _validate_with_critic(draft, context, generate)
    if not accepted:
        # One retry improves recoverability while keeping cost and latency bounded.
        try:
            refined = generate(build_refinement_prompt(prompt, draft, issues))
        except Exception:
            refined = None
        if isinstance(refined, dict):
            refined_accepted, _ = _validate_with_critic(refined, context, generate)
            if refined_accepted:
                draft, accepted = refined, True

    result = finalize_diagnosis(draft, context, accepted=accepted)
    # Similar retrieved examples may guide a hypothesis but never confirm a cause.
    result["diagnosis_cause"] = None
    result["advisory_examples"] = advisory
    return result
