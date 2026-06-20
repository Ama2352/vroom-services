import re, json, os
import requests as http_requests

DEFAULT_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-4-31b-it:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
]
MAX_STEPS = 5
OBS_LIMIT = 800
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_ACTION_RE = re.compile(r'Action:\s*(\w+)\((.+?)\)', re.DOTALL)
_FINAL_RE  = re.compile(r'Final Answer:\s*(\{.+\})', re.DOTALL | re.IGNORECASE)

_SYSTEM = """You are an SRE agent for the Vroom ride-hailing platform on Kubernetes.
Investigate the alert by calling tools. Use this EXACT format every time:

Thought: <your reasoning about what to check next>
Action: <tool_name>(key=value, key=value)

Available tools: get_pods, get_logs, get_events, describe_pod, get_metrics, get_traces, search_memory

Call search_memory early if the alert type looks familiar — skip it for novel failures you want to reason from scratch.
search_memory returns similar past incidents or "no relevant memory found".

When confident, output ONLY:
Final Answer: {"root_cause":"...","confidence":"HIGH|MEDIUM|LOW","remediation":{"tool":"restart_deployment","args":{"deployment":"...","namespace":"..."},"justification":"..."}}

Set remediation to null if no safe automated fix exists."""

_CORRECTION = {"role": "user", "content": (
    "That wasn't in the expected format. Use exactly:\n"
    "Thought: <reasoning>\n"
    "Action: tool_name(key=value, ...)"
)}


def _default_llm(messages: list, api_key: str, models: list = None) -> str:
    resp = http_requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"models": models or DEFAULT_MODELS, "messages": messages, "temperature": 0.1, "max_tokens": 512},
        timeout=30,
    )
    resp.raise_for_status()
    return (resp.json()["choices"][0]["message"].get("content") or "").strip()


def _parse_action(text: str) -> tuple:
    m = _ACTION_RE.search(text)
    if not m:
        return None, None
    tool_name = m.group(1)
    args = {}
    for pair in re.split(r',\s*', m.group(2)):
        if '=' in pair:
            k, v = pair.split('=', 1)
            args[k.strip()] = v.strip().strip('"').strip("'")
    return tool_name, args


def _parse_final(text: str) -> dict | None:
    cleaned = text.replace("```json", "").replace("```", "")
    m = _FINAL_RE.search(cleaned)
    if not m:
        return None
    raw = m.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def run_react_loop(alert: dict, call_tool_fn, api_key: str, *, models: list = None, _llm=None) -> dict:
    _active = models or DEFAULT_MODELS
    llm = _llm or (lambda msgs, key: _default_llm(msgs, key, _active))

    user_content = (
        f"Alert: {alert['alert_name']} on {alert['service']} (namespace={alert['namespace']})\n"
        f"Evidence: {alert['bundle']}\n"
        "Investigate."
    )
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",   "content": user_content},
    ]

    steps = []
    for _ in range(MAX_STEPS):
        try:
            response = llm(messages, api_key)
        except Exception:
            break

        final = _parse_final(response)
        if final is not None:
            final["investigation_steps"] = steps
            return final

        tool_name, tool_args = _parse_action(response)
        if tool_name is None:
            try:
                response = llm(messages + [_CORRECTION], api_key)
            except Exception:
                pass
            tool_name, tool_args = _parse_action(response)

        if tool_name is None:
            obs = "[tool call failed to parse — continuing]"
        else:
            try:
                obs = call_tool_fn(tool_name, tool_args or {})[:OBS_LIMIT]
            except Exception as e:
                obs = f"[tool error: {e}]"

        steps.append({"action": f"{tool_name}({tool_args})", "observation": obs})
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user",      "content": f"Observation: {obs}"})

    return {
        "root_cause": "Unable to determine — agent exhausted investigation steps",
        "confidence": "LOW",
        "remediation": None,
        "investigation_steps": steps,
    }
