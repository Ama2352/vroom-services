import re, json, os, time
import requests as http_requests

GROQ_URL       = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODELS = [
    {"id": "llama-3.3-70b-versatile",                   "provider": "groq"},
    {"id": "llama-3.1-8b-instant",                      "provider": "groq"},
    {"id": "meta-llama/llama-3.3-70b-instruct:free",    "provider": "openrouter"},
]

OBS_LIMIT = 800
_THINK_RE = re.compile(r'<think>.*?</think>|</think>', re.DOTALL)
_PLAN_RE  = re.compile(r'#E(\d+)\s*=\s*(\w+)\(([^)]*)\)', re.MULTILINE)

_MOCK_PLANS = {
    "scale_to_zero": (
        '#E1 = get_pods(namespace="{ns}", label_selector="app={svc}")\n'
        '#E2 = get_events(namespace="{ns}", service="{svc}")'
    ),
    "crashloop": (
        '#E1 = get_pods(namespace="{ns}", label_selector="app={svc}")\n'
        '#E2 = get_logs(service="{svc}", namespace="{ns}", tail=50)'
    ),
}
_MOCK_SOLVERS = {
    "scale_to_zero": {
        "root_cause":       "mock: deployment scaled to 0",
        "confidence":       "HIGH",
        "remediation_tool": "scale_deployment",
        "remediation_args": {},   # filled in at call time
        "justification":    "mock mode — scale_to_zero scenario",
    },
    "crashloop": {
        "root_cause":       "mock: container crash loop",
        "confidence":       "HIGH",
        "remediation_tool": "restart_deployment",
        "remediation_args": {},
        "justification":    "mock mode — crashloop scenario",
    },
}


def _mock_llm(messages: list, alert_name: str, service: str,
              namespace: str, scenario: str) -> str:
    """Returns canned Planner or Solver response. Detects call type from prompt content."""
    content = messages[0]["content"] if messages else ""
    if "investigation planner" in content:
        template = _MOCK_PLANS.get(scenario, _MOCK_PLANS["scale_to_zero"])
        return template.format(ns=namespace, svc=service)
    else:
        base = dict(_MOCK_SOLVERS.get(scenario, _MOCK_SOLVERS["scale_to_zero"]))
        base["remediation_args"] = {"deployment": service, "namespace": namespace}
        return json.dumps(base)


def _parse_plan(text: str, alert: dict) -> list:
    """Returns list of (tool_name, args_dict) ordered by step number.
    Falls back to [get_pods, get_events] using alert values if no valid steps found."""
    matches = _PLAN_RE.findall(text)
    if not matches:
        ns  = alert.get("namespace", "vroom-dev")
        svc = alert.get("service", "unknown")
        return [
            ("get_pods",   {"namespace": ns, "label_selector": f"app={svc}"}),
            ("get_events", {"namespace": ns, "service": svc}),
        ]
    steps = {}
    for num, tool_name, args_str in matches:
        args = {}
        for pair in args_str.split(","):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                args[k.strip()] = v.strip().strip('"').strip("'")
        steps[int(num)] = (tool_name.strip(), args)
    return [steps[k] for k in sorted(steps)]


def _parse_solver(content: str) -> dict | None:
    """Try json.loads directly, then scan for first {…} block."""
    if not content:
        return None
    try:
        return json.loads(content.strip())
    except json.JSONDecodeError:
        pass
    start = content.find("{")
    end   = content.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def _build_planner_prompt(alert_name: str, service: str, namespace: str,
                           bundle: str, memory_context: str) -> str:
    return f"""You are an SRE investigation planner for the Vroom ride-hailing platform on Kubernetes.

Alert: {alert_name}
Service: {service}
Namespace: {namespace}
Evidence bundle: {bundle}

Past incident memory (empty if none found):
{memory_context or "(none)"}

Available tools with exact parameter signatures:

  get_pods(namespace="{namespace}", label_selector="app={service}")
    Returns: NAME, READY, STATUS, RESTARTS, AGE for each pod

  get_events(namespace="{namespace}", service="{service}")
    Returns: recent Kubernetes events for {service} only (ScalingReplicaSet, OOMKilled, probe failures)

  get_logs(service="{service}", namespace="{namespace}", tail=50)
    Returns: last N log lines from pods matching app={service}

  get_traces(service="{service}")
    Returns: errored distributed trace summaries from Tempo (last 15 min)

  get_metrics(namespace="{namespace}")
    Returns: CPU(cores) and MEMORY(bytes) per pod

Write a numbered investigation plan. Each line must follow this EXACT format:
  #E1 = tool_name(param1="value1", param2="value2")

Rules:
  - Line 1 MUST always be: #E1 = get_pods(namespace="{namespace}", label_selector="app={service}")
  - Include get_events if #E1 returns no pods OR if you need to find why pods are missing
  - Include get_logs if pods are running but unhealthy
  - Include get_traces only if traces_errored > 0 in the evidence bundle above
  - Include get_metrics only if pods are running and resource pressure is suspected
  - If past memory already shows a confirmed fix for this exact pattern, 2 steps is enough
  - Maximum 5 steps total
  - Use only the tool names and parameter names listed above"""


def _build_solver_prompt(alert_name: str, service: str, namespace: str,
                          bundle: str, evidence_block: str) -> str:
    return f"""You are an SRE diagnosis expert for the Vroom ride-hailing platform.

Alert: {alert_name}
Service: {service}
Namespace: {namespace}
Evidence bundle: {bundle}

Investigation findings:
{evidence_block}

Based on ALL evidence above, output ONLY a valid JSON object — no markdown, no explanation:

If pods are missing (scaled to 0):
{{"root_cause":"...","confidence":"HIGH","remediation_tool":"scale_deployment","remediation_args":{{"deployment":"{service}","namespace":"{namespace}"}},"justification":"..."}}

If pods exist but are crash-looping or OOMKilled:
{{"root_cause":"...","confidence":"HIGH","remediation_tool":"restart_deployment","remediation_args":{{"deployment":"{service}","namespace":"{namespace}"}},"justification":"..."}}

If the failure requires manual intervention (dependency down, data corruption, config error):
{{"root_cause":"...","confidence":"MEDIUM","remediation_tool":"none","remediation_args":{{}},"justification":"..."}}

If evidence is insufficient to conclude:
{{"root_cause":"unable to determine from available evidence","confidence":"LOW","remediation_tool":"none","remediation_args":{{}},"justification":"..."}}"""


def _call_provider(messages: list, model_entry: dict, groq_key: str,
                   openrouter_key: str, max_tokens: int = 512) -> str:
    """Single LLM call to one provider+model. Retries once on 429."""
    if model_entry["provider"] == "groq":
        url, key = GROQ_URL, groq_key
    else:
        url, key = OPENROUTER_URL, openrouter_key

    delays = [0, 10]
    for i, delay in enumerate(delays):
        if delay:
            print(f"[rewoo:api] 429 on {model_entry['id']} — retrying in {delay}s", flush=True)
            time.sleep(delay)
        t0 = time.time()
        resp = http_requests.post(
            url,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={
                "model":       model_entry["id"],
                "messages":    messages,
                "temperature": 0.1,
                "max_tokens":  max_tokens,
            },
            timeout=30,
        )
        elapsed = time.time() - t0
        if resp.status_code == 429 and i < len(delays) - 1:
            continue
        resp.raise_for_status()
        print(f"[rewoo:api] provider={model_entry['provider']} model={model_entry['id']} "
              f"status={resp.status_code} duration={elapsed:.1f}s", flush=True)
        content = resp.json()["choices"][0]["message"].get("content") or ""
        return _THINK_RE.sub("", content).strip()
    return ""


def _default_llm_with_providers(messages: list, groq_key: str, openrouter_key: str,
                                  models: list, max_tokens: int = 512) -> str:
    """Try each model in order until one succeeds."""
    for model_entry in models:
        try:
            return _call_provider(messages, model_entry, groq_key, openrouter_key, max_tokens)
        except Exception as e:
            print(f"[rewoo:api] {model_entry['id']} failed: {e} — trying next", flush=True)
    print("[rewoo:api] all models failed", flush=True)
    return ""


def run_rewoo_loop(alert: dict, call_tool_fn, api_key: str,
                   *, models: list = None, _llm=None, groq_key: str = "") -> dict:
    """ReWOO loop: Planner → Worker → Solver.

    alert keys: alert_name, service, namespace, bundle, memory_context
    api_key:    OpenRouter API key (positional, kept for backward compat)
    groq_key:   Groq API key (keyword-only, defaults to empty string)
    Returns:    root_cause, confidence, remediation (dict|None), rewoo_steps (list)
    """
    _mock_mode     = os.environ.get("LLM_MOCK", "").lower() == "true"
    _mock_scenario = os.environ.get("LLM_MOCK_SCENARIO", "scale_to_zero")
    _active        = models or DEFAULT_MODELS

    alert_name = alert.get("alert_name", "").strip()
    service    = alert.get("service",    "").strip()
    namespace  = alert.get("namespace",  "").strip()
    bundle     = alert.get("bundle",     "")
    memory_ctx = alert.get("memory_context", "")

    def _call(messages, max_tokens=512):
        if _llm:
            return _llm(messages, api_key)
        if _mock_mode:
            return _mock_llm(messages, alert_name, service, namespace, _mock_scenario)
        return _default_llm_with_providers(messages, groq_key, api_key, _active, max_tokens)

    print(f"[rewoo] alert={alert_name} service={service} mock={_mock_mode}", flush=True)

    # ── Phase 1: Planner ──────────────────────────────────────────────────────
    if _mock_mode:
        print(f"[rewoo:planner] mock=True scenario={_mock_scenario}", flush=True)
    else:
        first = _active[0] if _active else {}
        print(f"[rewoo:planner] calling model={first.get('id','?')} provider={first.get('provider','?')}", flush=True)

    try:
        plan_text = _call(
            [{"role": "user",
              "content": _build_planner_prompt(alert_name, service, namespace,
                                               bundle, memory_ctx)}],
            max_tokens=256,
        )
    except Exception as e:
        print(f"[rewoo:planner] error: {e}", flush=True)
        plan_text = ""

    print(f"[rewoo:planner] raw={plan_text[:300]!r}", flush=True)
    plan = _parse_plan(plan_text, alert)
    print(f"[rewoo] plan={[(t, a) for t, a in plan]}", flush=True)

    # ── Phase 2: Worker ───────────────────────────────────────────────────────
    rewoo_steps = []
    for i, (tool_name, args) in enumerate(plan, 1):
        print(f"[rewoo:worker] E{i} action={tool_name} args={args}", flush=True)
        try:
            raw = call_tool_fn(tool_name, args)
            if len(raw) > OBS_LIMIT:
                cut = raw[:OBS_LIMIT].rfind('\n')
                obs = raw[:cut] if cut > 0 else raw[:OBS_LIMIT]
            else:
                obs = raw
        except Exception as e:
            obs = f"[tool error: {e}]"
        rewoo_steps.append({"action": f"{tool_name}({args})", "observation": obs})
        print(f"[rewoo:worker] E{i} obs={obs[:200]}", flush=True)

    # ── Phase 3: Solver ───────────────────────────────────────────────────────
    if not _mock_mode:
        time.sleep(2)  # throttle: spread 3 calls over ~6s, stays under 30 RPM

    evidence_lines = [
        f"#E{i} ({step['action'].split('(')[0]}): {step['observation']}"
        for i, step in enumerate(rewoo_steps, 1)
    ]
    if memory_ctx:
        evidence_lines.append(f"memory: {memory_ctx}")
    evidence_block = "\n".join(evidence_lines)

    if _mock_mode:
        print(f"[rewoo:solver] mock=True scenario={_mock_scenario}", flush=True)
    else:
        first = _active[0] if _active else {}
        print(f"[rewoo:solver] calling model={first.get('id','?')} provider={first.get('provider','?')}", flush=True)

    try:
        solver_text = _call(
            [{"role": "user",
              "content": _build_solver_prompt(alert_name, service, namespace,
                                              bundle, evidence_block)}],
            max_tokens=512,
        )
    except Exception as e:
        print(f"[rewoo:solver] error: {e}", flush=True)
        solver_text = ""

    print(f"[rewoo:solver] raw={solver_text[:300]!r}", flush=True)
    diagnosis = _parse_solver(solver_text)
    if diagnosis is None:
        print("[rewoo] solver parse failed — LOW confidence fallback", flush=True)
        diagnosis = {
            "root_cause":       "unable to determine from available evidence",
            "confidence":       "LOW",
            "remediation_tool": "none",
            "remediation_args": {},
            "justification":    "Solver output could not be parsed",
        }

    rem_tool = diagnosis.get("remediation_tool", "none")
    rem_args = diagnosis.get("remediation_args") or {}
    return {
        "root_cause":  diagnosis.get("root_cause", ""),
        "confidence":  diagnosis.get("confidence", "LOW"),
        "remediation": None if rem_tool == "none" else {
            "tool":          rem_tool,
            "args":          rem_args,
            "justification": diagnosis.get("justification", ""),
        },
        "rewoo_steps": rewoo_steps,
    }
