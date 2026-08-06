"""Deterministic safety gate for generated diagnoses."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class GateResult:
    passed: bool
    issues: list[str]
    status: str = "passed"


def validate_diagnosis(draft: dict, chain: dict) -> GateResult:
    issues: list[str] = []
    refs = draft.get("evidence_refs") or []
    known = {item["id"] for role in ("trigger", "primary", "causal_context", "consequence", "secondary") for item in chain.get(role, [])}
    issues.extend("unknown_evidence_reference" for ref in refs if ref not in known)
    for key in ("root_cause", "dev_action", "kubectl_hint"):
        if not isinstance(draft.get(key), str) or not draft[key].strip():
            issues.append(f"missing_{key}")
    primary_ids = {item["id"] for item in chain.get("primary", [])}
    if primary_ids and not (set(refs) & primary_ids):
        issues.append("missing_required_primary_reference")
    if chain.get("incident_kind") == "dlq" and not any(ref.startswith("log:") for ref in refs):
        issues.append("missing_required_primary_reference")
    if "readiness" in draft.get("root_cause", "").lower() and chain.get("incident_kind") == "dlq":
        issues.append("secondary_evidence_promoted_to_cause")
    if re.search(r"<[^>]+>", draft.get("root_cause", "")):
        issues.append("unresolved_placeholder")
    log_messages = [str(item.get("payload", {}).get("message", "")) for item in chain.get("primary", []) if item["id"].startswith("log:")]
    if log_messages and not any(message and message.lower() in draft.get("root_cause", "").lower() for message in log_messages):
        distinctive = " ".join(re.findall(r"[A-Za-z0-9_.-]{5,}", log_messages[0])).lower()
        if distinctive and not any(token in draft.get("root_cause", "").lower() for token in distinctive.split()[:2]):
            issues.append("missing_primary_error_signature")
    return GateResult(not issues, sorted(set(issues)), "passed" if not issues else "failed")
