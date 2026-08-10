"""Deterministic safety gate for generated diagnoses."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class GateResult:
    passed: bool
    issues: list[str]
    status: str = "passed"


def validate_diagnosis(draft: dict, context: dict) -> GateResult:
    issues: list[str] = []
    evidence = context.get("evidence") or []
    known = {item.get("id") for item in evidence if isinstance(item, dict)}
    refs = draft.get("evidence_refs") or []
    issues.extend("unknown_evidence_reference" for ref in refs if ref not in known)
    for key in ("root_cause", "dev_action", "kubectl_hint"):
        if not isinstance(draft.get(key), str) or not draft[key].strip():
            issues.append(f"missing_{key}")
    if evidence and not refs:
        issues.append("missing_evidence_reference")
    if any(re.search(r"<[^>]+>", str(draft.get(key, "")))
           for key in ("root_cause", "dev_action", "kubectl_hint")):
        issues.append("unresolved_placeholder")
    return GateResult(not issues, sorted(set(issues)), "passed" if not issues else "failed")
