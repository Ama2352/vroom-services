import re, json, os, time
import requests as http_requests

DEFAULT_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-4-31b-it:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
]
MAX_STEPS = 8
OBS_LIMIT = 800
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ── Text-fallback regexes (used when model returns no tool_calls) ─────────────
_ACTION_RE = re.compile(r'Action:\s*(\w+)\((.*?)\)', re.DOTALL)
_THINK_RE  = re.compile(r'<think>.*?</think>|</think>', re.DOTALL)
_FINAL_RE  = re.compile(r'Final Answer:\s*(\{.+\})', re.DOTALL | re.IGNORECASE)

# ── Tool schemas (OpenAI function-calling format) ─────────────────────────────
# Primary contract: the model returns structured tool_calls, no regex needed.
# Fallback: if model ignores tools, we parse free text (see _parse_action/_parse_final).
_TOOLS = [
    {"type": "function", "function": {
        "name": "get_pods",
        "description": "List pods in a namespace, optionally filtered by label selector.",
        "parameters": {"type": "object", "properties": {
            "namespace":      {"type": "string"},
            "label_selector": {"type": "string", "description": "e.g. app=ride-service"},
        }, "required": ["namespace"]},
    }},
    {"type": "function", "function": {
        "name": "get_logs",
        "description": "Fetch recent logs from pods matching a service label.",
        "parameters": {"type": "object", "properties": {
            "service":   {"type": "string"},
            "namespace": {"type": "string"},
            "tail":      {"type": "integer", "default": 50},
        }, "required": ["service", "namespace"]},
    }},
    {"type": "function", "function": {
        "name": "get_events",
        "description": "Get Kubernetes events for a namespace.",
        "parameters": {"type": "object", "properties": {
            "namespace": {"type": "string"},
        }, "required": ["namespace"]},
    }},
    {"type": "function", "function": {
        "name": "describe_pod",
        "description": "Describe a specific pod. Only call if the pod name appeared in get_pods output.",
        "parameters": {"type": "object", "properties": {
            "name":      {"type": "string", "description": "Full pod name from get_pods"},
            "namespace": {"type": "string"},
        }, "required": ["name", "namespace"]},
    }},
    {"type": "function", "function": {
        "name": "get_metrics",
        "description": "Get CPU/memory usage for pods in a namespace.",
        "parameters": {"type": "object", "properties": {
            "namespace": {"type": "string"},
        }, "required": ["namespace"]},
    }},
    {"type": "function", "function": {
        "name": "get_traces",
        "description": "Get recent error traces from Tempo for a service.",
        "parameters": {"type": "object", "properties": {
            "service":    {"type": "string"},
            "error_only": {"type": "boolean", "default": True},
        }, "required": ["service"]},
    }},
    {"type": "function", "function": {
        "name": "search_memory",
        "description": "Search past incident memory for similar alerts. Call early if alert type looks familiar.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "final_answer",
        "description": (
            "Call this when root cause is identified — do not call any other tool after this. "
            "Choose remediation_tool: "
            "scale_deployment when replicas=0 (no pods running, scale-down event); "
            "restart_deployment when pods exist but are crash-looping or OOMKilled; "
            "none when no safe automated fix exists."
        ),
        "parameters": {"type": "object", "properties": {
            "root_cause":       {"type": "string"},
            "confidence":       {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
            "remediation_tool": {"type": "string", "enum": ["restart_deployment", "scale_deployment", "none"]},
            "remediation_args": {
                "type": "object",
                "description": "{deployment, namespace} — add replicas (int) for scale_deployment",
            },
            "justification": {"type": "string"},
        }, "required": ["root_cause", "confidence", "remediation_tool", "justification"]},
    }},
]

_SYSTEM = """You are an SRE agent for the Vroom ride-hailing platform on Kubernetes.
Investigate the alert by calling the provided tools, one at a time.

Investigation order:
1. get_pods with label_selector=app=<service> — check if pods exist
2. If no pods → get_events — find why (scaled-down, OOM, eviction, crash)
3. If pods exist but unhealthy → get_logs or describe_pod
4. Call search_memory early when the alert type looks familiar

Stop as soon as evidence is clear — call final_answer immediately.
Never call describe_pod for a pod name that did not appear in get_pods output.

[Text fallback — only if tools are unavailable]
Thought: <reasoning>
Action: tool_name(key=value)
Final Answer: {"root_cause":"...","confidence":"HIGH|MEDIUM|LOW","remediation":{"tool":"restart_deployment|scale_deployment|none","args":{...},"justification":"..."}}"""

_CORRECTION = {"role": "user", "content": (
    "That wasn't in the expected format. Use exactly:\n"
    "Thought: <reasoning>\n"
    "Action: tool_name(key=value, ...)"
)}


def _default_llm(messages: list, api_key: str, models: list = None, use_tools: bool = True) -> dict:
    """Returns {"content": str, "tool_calls": list}. Retries on 429."""
    delays = [0, 5, 15]
    payload = {
        "models": models or DEFAULT_MODELS,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 512,
    }
    if use_tools:
        payload["tools"] = _TOOLS
        payload["tool_choice"] = "auto"

    for i, delay in enumerate(delays):
        if delay:
            print(f"[react] 429 rate-limited — retrying in {delay}s (attempt {i+1}/{len(delays)})", flush=True)
            time.sleep(delay)
        resp = http_requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        if resp.status_code == 429 and i < len(delays) - 1:
            continue
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        return {
            "content":    _clean(msg.get("content") or ""),
            "tool_calls": msg.get("tool_calls") or [],
        }


def _clean(text: str) -> str:
    return _THINK_RE.sub('', text).strip()


def _parse_action(text: str) -> tuple:
    matches = _ACTION_RE.findall(text)
    if not matches:
        return None, None
    # Take the last match — reasoning models often self-correct within a single response
    tool_name, args_str = matches[-1]
    args = {}
    for pair in re.split(r',\s*', args_str):
        if '=' in pair:
            k, v = pair.split('=', 1)
            args[k.strip()] = v.strip().strip('"').strip("'")
    return tool_name, args


def _parse_final(text: str) -> dict | None:
    cleaned = text.replace("```json", "").replace("```", "")
    m = _FINAL_RE.search(cleaned)
    if not m:
        return None
    try:
        return json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return None


def run_react_loop(alert: dict, call_tool_fn, api_key: str, *, models: list = None, _llm=None) -> dict:
    _active = models or DEFAULT_MODELS

    def _call_llm(msgs, use_tools=True):
        if _llm:
            raw = _llm(msgs, api_key)
            return {"content": _clean(raw) if isinstance(raw, str) else str(raw), "tool_calls": []}
        return _default_llm(msgs, api_key, _active, use_tools)

    user_content = (
        f"Alert: {alert['alert_name'].strip()} on {alert['service'].strip()} (namespace={alert['namespace'].strip()})\n"
        f"Evidence: {alert['bundle']}\n"
        "Investigate."
    )
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",   "content": user_content},
    ]

    print(f"[react] starting: {alert['alert_name'].strip()} / {alert['service'].strip()} models={_active}", flush=True)

    steps = []
    completed_steps = 0

    for step_n in range(MAX_STEPS):
        try:
            msg = _call_llm(messages)
        except Exception as e:
            print(f"[react] step={step_n} LLM ERROR: {e}", flush=True)
            break
        completed_steps += 1

        tool_calls = msg["tool_calls"]
        content    = msg["content"]

        if tool_calls:
            # ── Structured path: model returned tool_calls ────────────────────
            tc        = tool_calls[0]
            func      = tc.get("function", {})
            tool_name = func.get("name", "")
            try:
                tool_args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                tool_args = {}

            print(f"[react] step={step_n} [tool_call] {tool_name}({json.dumps(tool_args)})", flush=True)

            if tool_name == "final_answer":
                rem_tool = tool_args.get("remediation_tool", "none")
                rem_args = tool_args.get("remediation_args") or {}
                result = {
                    "root_cause":          tool_args.get("root_cause", ""),
                    "confidence":          tool_args.get("confidence", "LOW"),
                    "remediation":         None if rem_tool == "none" else {
                        "tool":          rem_tool,
                        "args":          rem_args,
                        "justification": tool_args.get("justification", ""),
                    },
                    "investigation_steps": steps,
                }
                print(f"[react] step={step_n} Final Answer via tool call (confidence={result['confidence']})", flush=True)
                return result

            try:
                obs = call_tool_fn(tool_name, tool_args)[:OBS_LIMIT]
                print(f"[react] step={step_n} obs={obs[:120]}", flush=True)
            except Exception as e:
                obs = f"[tool error: {e}]"
                print(f"[react] step={step_n} tool={tool_name} ERROR: {e}", flush=True)

            steps.append({"action": f"{tool_name}({tool_args})", "observation": obs})
            tc_id = tc.get("id") or f"call_{step_n}"
            messages.append({
                "role":       "assistant",
                "content":    content or None,
                "tool_calls": tool_calls,
            })
            messages.append({
                "role":         "tool",
                "tool_call_id": tc_id,
                "content":      obs,
            })

        else:
            # ── Text fallback: model didn't return tool_calls ─────────────────
            print(f"[react] step={step_n} [text_fallback]\n{content}\n---", flush=True)

            final = _parse_final(content)
            if final is not None:
                print(f"[react] step={step_n} Final Answer parsed OK (text fallback)", flush=True)
                final["investigation_steps"] = steps
                return final

            tool_name, tool_args = _parse_action(content)
            if tool_name is None:
                print(f"[react] step={step_n} parse failed — sending correction", flush=True)
                try:
                    corr    = _call_llm(messages + [_CORRECTION], use_tools=False)
                    content2 = corr["content"]
                    print(f"[react] step={step_n} correction:\n{content2}\n---", flush=True)
                except Exception as e:
                    print(f"[react] step={step_n} correction LLM ERROR: {e}", flush=True)
                    content2 = ""
                tool_name, tool_args = _parse_action(content2)

            if tool_name is None:
                obs = "[tool call failed to parse — continuing]"
                print(f"[react] step={step_n} still no valid action after correction", flush=True)
            else:
                try:
                    obs = call_tool_fn(tool_name, tool_args or {})[:OBS_LIMIT]
                    print(f"[react] step={step_n} tool={tool_name} obs={obs[:120]}", flush=True)
                except Exception as e:
                    obs = f"[tool error: {e}]"
                    print(f"[react] step={step_n} tool={tool_name} ERROR: {e}", flush=True)

            steps.append({"action": f"{tool_name}({tool_args})", "observation": obs})
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user",      "content": f"Observation: {obs}"})

    print(f"[react] stopped after {completed_steps}/{MAX_STEPS} steps without Final Answer", flush=True)
    return {
        "root_cause":          "Unable to determine — agent exhausted investigation steps",
        "confidence":          "LOW",
        "remediation":         None,
        "investigation_steps": steps,
    }
