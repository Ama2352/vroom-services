"""Second validation: ask an LLM whether a draft is grounded in current evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class CriticResult:
    """A failed or unavailable critic never publishes an unsupported answer."""

    passed: bool
    issues: list[str]
    status: str


def run_semantic_critic(
    context: dict,
    draft: dict,
    *,
    generate: Callable[[str], dict] | None,
) -> CriticResult:
    """Check whether the proposed cause and action follow from current evidence."""
    if generate is None:
        return CriticResult(False, ["critic_unavailable"], "unavailable")

    prompt = "\n".join([
        "You are an independent incident-diagnosis critic.",
        "Check that diagnosis_cause and recommended_action are supported by CURRENT EVIDENCE.",
        "A hypothesis must remain unconfirmed and cite its own evidence.",
        'Return only JSON: {"verdict":"pass"|"fail","issues":["short_reason"]}.',
        "CURRENT EVIDENCE:", json.dumps(context, sort_keys=True),
        "DRAFT:", json.dumps(draft, sort_keys=True),
    ])
    try:
        raw = generate(prompt)
    except Exception:
        return CriticResult(False, ["critic_unavailable"], "unavailable")
    return _parse_critic_result(raw)


def _parse_critic_result(raw: object) -> CriticResult:
    """Treat malformed critic output as a failed check rather than trusting it."""
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return CriticResult(False, ["invalid_critic_json"], "invalid")

    if not isinstance(payload, dict) or payload.get("verdict") not in {"pass", "fail"}:
        return CriticResult(False, ["invalid_critic_verdict"], "invalid")
    issues = payload.get("issues", [])
    if not isinstance(issues, list) or any(not isinstance(issue, str) for issue in issues):
        return CriticResult(False, ["invalid_critic_issues"], "invalid")

    passed = payload["verdict"] == "pass" and not issues
    return CriticResult(passed, issues, "passed" if passed else "failed")
