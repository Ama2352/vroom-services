"""Independent semantic critic contract with strict JSON parsing."""

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class CriticResult:
    passed: bool
    issues: list[str]
    status: str


def parse_critic_output(raw) -> CriticResult:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return CriticResult(False, ["invalid_critic_json"], "invalid")
    if not isinstance(payload, dict) or payload.get("verdict") not in {"pass", "fail"}:
        return CriticResult(False, ["invalid_critic_verdict"], "invalid")
    issues = payload.get("issues", [])
    if not isinstance(issues, list) or any(not isinstance(item, str) for item in issues):
        return CriticResult(False, ["invalid_critic_issues"], "invalid")
    passed = payload["verdict"] == "pass" and not issues
    return CriticResult(passed, issues, "passed" if passed else "failed")


def run_semantic_critic(chain: dict, draft: dict, _llm=None) -> CriticResult:
    if _llm is None:
        return CriticResult(False, ["critic_unavailable"], "unavailable")
    prompt = json.dumps({"task": "criticize diagnosis", "chain": chain, "draft": draft}, sort_keys=True)
    try:
        try:
            raw = _llm(prompt, temperature=0.0)
        except TypeError:
            raw = _llm(prompt)
    except Exception:
        return CriticResult(False, ["critic_unavailable"], "unavailable")
    if not raw:
        return CriticResult(False, ["critic_unavailable"], "unavailable")
    return parse_critic_output(raw)
