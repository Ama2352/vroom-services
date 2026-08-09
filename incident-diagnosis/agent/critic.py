"""Independent semantic critic contract with strict JSON parsing."""

import json
from dataclasses import dataclass


_MAX_TEXT_CHARS = 500
_MAX_LIST_ITEMS = 12
_OMITTED_KEYS = {
    "patch", "changed_files", "diff", "diff_snippet", "raw", "bundle",
    "authorization", "token", "headers",
}


@dataclass(frozen=True)
class CriticResult:
    passed: bool
    issues: list[str]
    status: str


def _bounded_value(value):
    if isinstance(value, str):
        return value[:_MAX_TEXT_CHARS]
    if isinstance(value, list):
        return [_bounded_value(item) for item in value[:_MAX_LIST_ITEMS]]
    if isinstance(value, dict):
        return {
            str(key): _bounded_value(item)
            for key, item in value.items()
            if str(key).lower() not in _OMITTED_KEYS
        }
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:_MAX_TEXT_CHARS]


def _critic_chain_view(chain: dict) -> dict:
    """Build a bounded semantic view; repository source belongs to causality collection."""
    roles = ("trigger", "primary", "causal_context", "consequence", "secondary", "contradictions")
    return {
        "incident_kind": str(chain.get("incident_kind", "unknown")),
        "required": list(chain.get("required", []))[:_MAX_LIST_ITEMS],
        **{
            role: [_bounded_value(item) for item in (chain.get(role, []) or [])[:_MAX_LIST_ITEMS]]
            for role in roles
        },
    }


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
    prompt = "\n".join([
        "You are an independent incident-diagnosis critic.",
        "Check whether the cause explains the trigger, respects evidence roles, avoids unsupported causal promotion, and recommends relevant remediation.",
        "Return ONLY JSON matching one of these exact shapes (no markdown or explanation):",
        '{"verdict":"pass","issues":[]}',
        '{"verdict":"fail","issues":["concise_issue_code_or_reason"]}',
        json.dumps({"chain": _critic_chain_view(chain), "draft": _bounded_value(draft)}, sort_keys=True),
    ])
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
