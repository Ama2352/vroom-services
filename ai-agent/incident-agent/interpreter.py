import re, json
import requests as http_requests

GROQ_URL       = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODELS = [
    {"id": "llama-3.3-70b-versatile",                "provider": "groq"},
    {"id": "llama-3.1-8b-instant",                   "provider": "groq"},
    {"id": "meta-llama/llama-3.3-70b-instruct:free", "provider": "openrouter"},
]

K8S_KNOWLEDGE_TABLE = """\
Kubernetes pod waiting reasons and their diagnostic signatures:
- PodInitializing: Init containers are running and blocking pod startup.
  Look for: which init container is stuck, missing Secret/ConfigMap it depends on,
  external service unreachable during init. Primary source: init container logs.
- CrashLoopBackOff: Container started but exited with non-zero exit code, repeatedly.
  Look for: application crash on startup, missing required env var, OOM at startup,
  dependency (DB, Redis) unreachable. Primary source: previous container logs (--previous).
- OOMKilled: Container exceeded its memory limit and was killed by the kernel.
  No application logs after kill point. Look for: memory limit in pod spec vs. actual usage.
- ImagePullBackOff / ErrImagePull: Registry cannot pull the container image.
  Look for: typo in image name/tag, private registry credentials missing, image deleted.
  Primary source: K8s event message (names the exact image).
- CreateContainerConfigError: Pod spec references a Secret or ConfigMap that does not exist.
  The K8s event message names the missing resource explicitly.
- Pending with FailedScheduling event: No node can schedule the pod.
  Look for: node selector mismatch, insufficient CPU/memory on all nodes, PodAffinity/Taint rules.
- (empty waiting_reason, available=0, desired>0): Deployment has zero running replicas.
  Look for: explicit scale-to-zero, HPA scale-down, manual kubectl scale."""

GROUNDING_RULE = """\
GROUNDING RULE: You may ONLY assert claims directly supported by one or more of the \
evidence fields listed above. Do not invent component names, service names, port numbers, \
error messages, or failure causes that are not present in the evidence.

If the evidence is insufficient to identify a specific root cause, set root_cause to \
exactly: "Insufficient evidence: need [the specific data that would clarify this]"

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
        K8S_KNOWLEDGE_TABLE,
        "",
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
        lines.append(
            f"  Container state: {facts['waiting_reason']} ({facts['restarts']} restarts)"
        )
    if facts.get("log_error"):
        lines.append(f"  Last error log: {facts['log_error']}")
    if facts.get("event_reason"):
        lines.append(
            f"  Last K8s event: {facts['event_reason']} on "
            f"{facts.get('event_object', '?')} — {facts.get('event_message', '')}"
        )
    if bundle:
        lines.append(f"  Service metrics (5 min): {bundle}")
    if memory_context:
        lines += ["", f"Past similar incidents:\n{memory_context}"]
    lines += [
        "",
        GROUNDING_RULE,
        "",
        "Output exactly this JSON (no markdown, no explanation):",
        '{"root_cause":"...","dev_action":"...","kubectl_hint":"..."}',
    ]
    return "\n".join(lines)


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


def _fallback(service: str, namespace: str, facts: dict) -> dict:
    reason = facts.get("waiting_reason") or "Unknown state"
    log    = facts.get("log_error")     or "no log available"
    return {
        "root_cause":   f"{reason} — {log}",
        "dev_action":   "Investigate manually using kubectl.",
        "kubectl_hint": f"kubectl describe pod -n {namespace} -l app={service}",
    }


def _call_llm(messages: list, model_entry: dict,
              groq_key: str, openrouter_key: str, max_tokens: int = 200) -> str:
    url = GROQ_URL if model_entry["provider"] == "groq" else OPENROUTER_URL
    key = groq_key if model_entry["provider"] == "groq" else openrouter_key
    resp = http_requests.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model_entry["id"], "messages": messages,
              "temperature": 0.1, "max_tokens": max_tokens},
        timeout=30,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"].get("content") or ""
    return _THINK_RE.sub("", content).strip()


def interpret(
    alert_name: str, service: str, namespace: str,
    facts: dict, bundle: str, memory_context: str,
    models: list, groq_key: str = "", openrouter_key: str = "",
    _llm=None,
) -> dict:
    """Interpret structured pod facts into a 3-field diagnosis via one LLM call.

    Falls back to a Python-derived answer when the LLM is unavailable or returns
    invalid output. Always returns dict with root_cause, dev_action, kubectl_hint.
    """
    prompt   = _build_grounded_prompt(alert_name, service, namespace, facts, bundle, memory_context, "")
    messages = [{"role": "user", "content": prompt}]

    if _llm is not None:
        try:
            raw = _llm(messages, openrouter_key)
        except Exception:
            raw = ""
    else:
        raw = ""
        for model_entry in (models or DEFAULT_MODELS):
            try:
                raw = _call_llm(messages, model_entry, groq_key, openrouter_key)
                break
            except Exception as exc:
                print(f"[interpreter] {model_entry['id']} failed: {exc}", flush=True)

    result = _parse_output(raw)
    if result is None:
        print(f"[interpreter] parse failed — using fallback. raw={raw[:200]!r}", flush=True)
        return _fallback(service, namespace, facts)
    return result
