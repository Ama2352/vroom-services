import re, json, os, time
import requests as http_requests

DEFAULT_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-4-31b-it:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
]

OBS_LIMIT      = 800
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_THINK_RE      = re.compile(r'<think>.*?</think>|</think>', re.DOTALL)
_PLAN_RE       = re.compile(r'#E(\d+)\s*=\s*(\w+)\(([^)]*)\)', re.MULTILINE)


def _parse_plan(text: str, alert: dict) -> list:
    """Returns list of (tool_name, args_dict) ordered by step number.
    Falls back to [get_pods, get_events] using alert values if no valid steps found."""
    matches = _PLAN_RE.findall(text)
    if not matches:
        ns  = alert.get("namespace", "vroom-dev")
        svc = alert.get("service", "unknown")
        return [
            ("get_pods",   {"namespace": ns, "label_selector": f"app={svc}"}),
            ("get_events", {"namespace": ns}),
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

  get_events(namespace="{namespace}")
    Returns: recent Kubernetes events (ScalingReplicaSet, OOMKilled, probe failures)

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


def _default_llm(messages: list, api_key: str, models: list,
                 max_tokens: int = 512) -> str:
    """Single LLM call returning text. Retries once on 429."""
    delays  = [0, 10]
    payload = {
        "models":      models,
        "messages":    messages,
        "temperature": 0.1,
        "max_tokens":  max_tokens,
    }
    for i, delay in enumerate(delays):
        if delay:
            print(f"[rewoo] 429 — retrying in {delay}s", flush=True)
            time.sleep(delay)
        resp = http_requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        if resp.status_code == 429 and i < len(delays) - 1:
            continue
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"].get("content") or ""
        return _THINK_RE.sub("", content).strip()
    return ""


def run_rewoo_loop(alert: dict, call_tool_fn, api_key: str,
                   *, models: list = None, _llm=None) -> dict:
    """ReWOO loop: Planner → Worker → Solver.

    alert keys: alert_name, service, namespace, bundle, memory_context
    Returns:    root_cause, confidence, remediation (dict|None), rewoo_steps (list)
    """
    _active    = models or DEFAULT_MODELS
    alert_name = alert.get("alert_name", "").strip()
    service    = alert.get("service",    "").strip()
    namespace  = alert.get("namespace",  "").strip()
    bundle     = alert.get("bundle",     "")
    memory_ctx = alert.get("memory_context", "")

    def _call(messages, max_tokens=512):
        if _llm:
            return _llm(messages, api_key)
        return _default_llm(messages, api_key, _active, max_tokens)

    print(f"[rewoo] alert={alert_name} service={service} models={_active}", flush=True)

    # ── Phase 1: Planner ──────────────────────────────────────────────────────
    try:
        plan_text = _call(
            [{"role": "user",
              "content": _build_planner_prompt(alert_name, service, namespace,
                                               bundle, memory_ctx)}],
            max_tokens=256,
        )
    except Exception as e:
        print(f"[rewoo] planner error: {e}", flush=True)
        plan_text = ""

    plan = _parse_plan(plan_text, alert)
    print(f"[rewoo] plan={[(t, a) for t, a in plan]}", flush=True)

    # ── Phase 2: Worker ───────────────────────────────────────────────────────
    rewoo_steps = []
    for i, (tool_name, args) in enumerate(plan, 1):
        try:
            obs = call_tool_fn(tool_name, args)[:OBS_LIMIT]
        except Exception as e:
            obs = f"[tool error: {e}]"
        rewoo_steps.append({"action": f"{tool_name}({args})", "observation": obs})
        print(f"[rewoo] E{i} {tool_name} obs={obs[:200]}", flush=True)

    # ── Phase 3: Solver ───────────────────────────────────────────────────────
    evidence_lines = [
        f"#E{i} ({step['action'].split('(')[0]}): {step['observation']}"
        for i, step in enumerate(rewoo_steps, 1)
    ]
    if memory_ctx:
        evidence_lines.append(f"memory: {memory_ctx}")
    evidence_block = "\n".join(evidence_lines)

    try:
        solver_text = _call(
            [{"role": "user",
              "content": _build_solver_prompt(alert_name, service, namespace,
                                              bundle, evidence_block)}],
            max_tokens=512,
        )
    except Exception as e:
        print(f"[rewoo] solver error: {e}", flush=True)
        solver_text = ""

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
