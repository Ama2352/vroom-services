import re, json
import requests as http_requests

GROQ_URL       = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODELS = [
    {"id": "llama-3.3-70b-versatile",                "provider": "groq"},
    {"id": "llama-3.1-8b-instant",                   "provider": "groq"},
    {"id": "meta-llama/llama-3.3-70b-instruct:free", "provider": "openrouter"},
]

REFINE_TEMPERATURE = 0.4

GROUNDING_RULE = """\
GROUNDING RULE: You may ONLY assert claims directly supported by one or more of the \
evidence fields listed above. Do not invent component names, service names, port numbers, \
error messages, or failure causes that are not present in the evidence.

If the evidence is insufficient to identify a definitive root cause, root_cause must still \
restate the specific observed symptom, in exactly this form: "Insufficient evidence to \
confirm — observed: [exact detail from the evidence, e.g. hostname/port/error text]. Need \
[specific additional data]." Never ask for information that is already present in the \
evidence above.

Do not guess. An honest sparse answer is more useful than a confident hallucination."""

GENERIC_ROOT_CAUSE = [
    "potential issue", "possible issue", "might be", "could be",
    "seems to be", "appears to be", "investigate the", "check the",
    "there may be", "there might be",
]
GENERIC_DEV_ACTION = [
    "investigate manually", "check the logs", "look into the",
    "investigate the pod", "check for any errors",
]

_THINK_RE     = re.compile(r"<think>.*?</think>|</think>", re.DOTALL)
REQUIRED_KEYS = {"root_cause", "dev_action", "kubectl_hint"}


def _build_grounded_prompt(alert_name: str, service: str, namespace: str,
                            facts: dict, bundle: str, memory_context: str,
                            pod: str) -> str:
    lines = [
        f"Alert: {alert_name}",
        f"Service: {service}",
        f"Namespace: {namespace}",
    ]
    if pod:
        lines.append(f"Pod: {pod}")
    lines += [
        "",
        "Evidence:",
        f"  Pods: {facts['pods_available']}/{facts['pods_desired']} running",
    ]
    if facts.get("waiting_reason"):
        state_line = f"  Container state: {facts['waiting_reason']} ({facts['restarts']} restarts)"
        if facts.get("last_terminated_reason"):
            state_line += f" — last exit: {facts['last_terminated_reason']}"
        lines.append(state_line)
    elif facts.get("last_terminated_reason"):
        lines.append(f"  Last exit reason: {facts['last_terminated_reason']} ({facts['restarts']} restarts)")
    if facts.get("init_waiting_reason") or facts.get("init_last_terminated_reason"):
        init_line = f"  Init container: {facts.get('init_waiting_reason') or 'waiting'} ({facts.get('init_restarts', 0)} restarts)"
        if facts.get("init_last_terminated_reason"):
            init_line += f" — last exit: {facts['init_last_terminated_reason']}"
        lines.append(init_line)
    if facts.get("log_error"):
        lines.append(f"  Last error log: {facts['log_error']}")
    if facts.get("event_reason"):
        lines.append(
            f"  Last K8s event: {facts['event_reason']} on "
            f"{facts.get('event_object', '?')} — {facts.get('event_message', '')}"
        )
    if facts.get("template_diff"):
        td = facts["template_diff"]
        if td.get("env_changed"):
            diffs = "; ".join(
                f"{d['key']}: {d['old_value']} → {d['new_value']}" for d in td["env_diff"]
            )
            lines.append(f"  Recent change: env changed — {diffs}")
        elif td.get("image_changed"):
            lines.append(f"  Recent change: image changed from {td['old_image']} to {td['new_image']}")
    if facts.get("dependency"):
        dep = facts["dependency"]
        dep_line = (
            f"  Dependency {dep['name']}.{dep['namespace']}: "
            f"{dep['pods_available']}/{dep['pods_desired']} pods running"
        )
        if dep.get("waiting_reason"):
            dep_line += f" ({dep['waiting_reason']})"
        lines.append(dep_line)
    if bundle:
        lines.append(f"  Service metrics (5 min): {bundle}")
    if memory_context:
        lines += [
            "",
            "Trusted match from the knowledge base (human-approved — use as your basis):",
            memory_context,
            "",
            "This is a previously confirmed failure pattern whose K8s state matches the current "
            "evidence. Use it as the basis for your root_cause and dev_action unless the Evidence "
            "section above clearly contradicts it.",
        ]
    lines += [
        "",
        GROUNDING_RULE,
        "",
        "Output exactly this JSON (no markdown, no explanation):",
        '{"root_cause":"...","dev_action":"...","kubectl_hint":"..."}',
    ]
    return "\n".join(lines)


_MIN_GROUNDING_TOKEN_LEN = 5


def _tokenize_for_grounding(text: str) -> set:
    return {t for t in re.findall(r"[a-zA-Z0-9_./:-]+", text.lower())
            if len(t) >= _MIN_GROUNDING_TOKEN_LEN}


def _is_grounded(root_cause: str, facts: dict) -> bool:
    evidence_parts = [facts.get("log_error", ""), facts.get("event_message", "")]
    td = facts.get("template_diff")
    if td:
        for d in td.get("env_diff", []):
            evidence_parts.append(d.get("old_value", ""))
            evidence_parts.append(d.get("new_value", ""))
        evidence_parts.append(td.get("old_image", ""))
        evidence_parts.append(td.get("new_image", ""))
    dep = facts.get("dependency")
    if dep and dep.get("name"):
        evidence_parts.append(dep["name"])

    evidence_text = " ".join(s for s in evidence_parts if s)
    if not evidence_text:
        return True
    evidence_tokens = _tokenize_for_grounding(evidence_text)
    if not evidence_tokens:
        return True
    return bool(evidence_tokens & _tokenize_for_grounding(root_cause))


def _quality_check(diagnosis: dict, facts: dict, pod: str, service: str) -> dict:
    rc     = diagnosis.get("root_cause",   "").lower()
    da     = diagnosis.get("dev_action",   "").lower()
    kh     = diagnosis.get("kubectl_hint", "")
    issues = []

    if "<" in kh and ">" in kh:
        replacement = pod if pod else f"-l app={service}"
        issues.append(
            f"kubectl_hint contains a placeholder — replace with actual value: "
            f"'{replacement}'"
        )

    if rc.startswith("insufficient evidence"):
        # Honest low-confidence response — skip the phrase-blacklist/dev_action
        # checks below, but it must still cite the specific evidence it's hedging
        # about (placeholder issue found above, if any, also still applies).
        if not _is_grounded(rc, facts):
            issues.append(
                "even when uncertain, root_cause must restate the specific observed symptom "
                "from the evidence (exact error text, hostname, or port) — do not just ask "
                "for information that is already present in the evidence above"
            )
        return {"passed": len(issues) == 0, "low_confidence": True, "issues": issues}

    if not _is_grounded(rc, facts):
        issues.append(
            "root_cause is not grounded in the available evidence (log_error/event_message) — "
            "reference specific text from those fields or lower confidence"
        )

    if any(p in rc for p in GENERIC_ROOT_CAUSE):
        issues.append(
            "root_cause uses vague language — it must name a specific cause "
            "drawn from the evidence (component, error, or resource name)"
        )

    if any(p in da for p in GENERIC_DEV_ACTION):
        issues.append(
            "dev_action is too vague — state the specific action "
            "(e.g. 'check init container logs', 'verify Secret X exists')"
        )

    return {"passed": len(issues) == 0, "low_confidence": False, "issues": issues}


def _parse_output(text: str) -> dict | None:
    if not text:
        return None
    try:
        result = json.loads(text.strip())
    except json.JSONDecodeError:
        start = text.find("{")
        end   = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            result = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(result, dict):
        return None
    if not REQUIRED_KEYS.issubset(result.keys()):
        return None
    if not all(isinstance(result[k], str) and result[k].strip() for k in REQUIRED_KEYS):
        return None
    return result


def _build_refine_prompt(original_prompt: str, diagnosis: dict, issues: list) -> str:
    lines = [
        original_prompt,
        "",
        "Your previous answer was:",
        json.dumps(diagnosis, indent=2),
        "",
        "The following specific issues were detected:",
    ]
    for issue in issues:
        lines.append(f"- {issue}")
    lines += [
        "",
        "Fix ONLY these issues. Keep all other fields the same.",
        "Output exactly this JSON (no markdown, no explanation):",
        '{"root_cause":"...","dev_action":"...","kubectl_hint":"..."}',
    ]
    return "\n".join(lines)


def _run_llm(messages: list, _llm, models: list,
             groq_key: str, openrouter_key: str, temperature: float = 0.1) -> str:
    if _llm is not None:
        try:
            return _llm(messages, openrouter_key)
        except Exception:
            return ""
    for model_entry in (models or DEFAULT_MODELS):
        try:
            return _call_llm(messages, model_entry, groq_key, openrouter_key, temperature=temperature)
        except Exception as exc:
            print(f"[interpreter] {model_entry['id']} failed: {exc}", flush=True)
    return ""


def _fallback(service: str, namespace: str, facts: dict) -> dict:
    reason = facts.get("waiting_reason") or "Unknown state"
    log    = facts.get("log_error")     or "no log available"
    return {
        "root_cause":   f"{reason} — {log}",
        "dev_action":   "Investigate manually using kubectl.",
        "kubectl_hint": f"kubectl describe pod -n {namespace} -l app={service}",
    }


def _call_llm(messages: list, model_entry: dict,
              groq_key: str, openrouter_key: str, max_tokens: int = 400,
              temperature: float = 0.1) -> str:
    url = GROQ_URL if model_entry["provider"] == "groq" else OPENROUTER_URL
    key = groq_key if model_entry["provider"] == "groq" else openrouter_key
    resp = http_requests.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model_entry["id"], "messages": messages,
              "temperature": temperature, "max_tokens": max_tokens},
        timeout=30,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"].get("content") or ""
    return _THINK_RE.sub("", content).strip()


def interpret(
    alert_name: str, service: str, namespace: str,
    facts: dict, bundle: str, memory_context: str,
    models: list, groq_key: str = "", openrouter_key: str = "",
    pod: str = "", _llm=None,
) -> dict:
    prompt   = _build_grounded_prompt(alert_name, service, namespace,
                                      facts, bundle, memory_context, pod)
    messages = [{"role": "user", "content": prompt}]

    # Phase 1 — Grounded Generation
    raw    = _run_llm(messages, _llm, models, groq_key, openrouter_key)
    phase1 = _parse_output(raw)
    if phase1 is None:
        print(f"[interpreter] parse failed — using fallback. raw={raw[:600]!r}", flush=True)
        result = _fallback(service, namespace, facts)
        result["low_confidence"] = False
        return result

    # Phase 2 — Deterministic Quality Check
    qc = _quality_check(phase1, facts, pod, service)
    if qc["passed"]:
        phase1["low_confidence"] = qc["low_confidence"]
        return phase1

    # Phase 3 — Targeted Self-Refine
    print(f"[interpreter] quality issues detected: {qc['issues']}", flush=True)
    refine_prompt   = _build_refine_prompt(prompt, phase1, qc["issues"])
    refine_messages = [{"role": "user", "content": refine_prompt}]
    raw2    = _run_llm(refine_messages, _llm, models, groq_key, openrouter_key,
                       temperature=REFINE_TEMPERATURE)
    refined = _parse_output(raw2)
    if refined is None:
        print(f"[interpreter] refine parse failed — returning phase1 output", flush=True)
        phase1["low_confidence"] = False
        return phase1
    refined["low_confidence"] = False
    return refined
