import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import rewoo_loop

ALERT = {
    "alert_name":     "HighErrorRate",
    "service":        "ride-service",
    "namespace":      "vroom-dev",
    "bundle":         "rps=0 err=0% traces_errored=0",
    "memory_context": "",
}


# ── _parse_plan ────────────────────────────────────────────────────────────────

def test_parse_plan_extracts_steps():
    text = (
        '#E1 = get_pods(namespace="vroom-dev", label_selector="app=ride-service")\n'
        '#E2 = get_events(namespace="vroom-dev")\n'
    )
    steps = rewoo_loop._parse_plan(text, ALERT)
    assert len(steps) == 2
    assert steps[0][0] == "get_pods"
    assert steps[0][1]["namespace"] == "vroom-dev"
    assert steps[0][1]["label_selector"] == "app=ride-service"
    assert steps[1][0] == "get_events"


def test_parse_plan_strips_quotes_from_values():
    text = '#E1 = get_logs(service="ride-service", namespace="vroom-dev", tail=50)\n'
    steps = rewoo_loop._parse_plan(text, ALERT)
    assert steps[0][1]["service"] == "ride-service"
    assert steps[0][1]["namespace"] == "vroom-dev"
    assert steps[0][1]["tail"] == "50"


def test_parse_plan_empty_returns_fallback():
    steps = rewoo_loop._parse_plan("I cannot create a plan.", ALERT)
    assert len(steps) >= 2
    assert steps[0][0] == "get_pods"
    assert steps[0][1]["label_selector"] == "app=ride-service"
    assert steps[1][0] == "get_events"


def test_parse_plan_fallback_uses_alert_namespace():
    alert = {**ALERT, "namespace": "vroom-prod", "service": "dispatch-service"}
    steps = rewoo_loop._parse_plan("", alert)
    assert steps[0][1]["namespace"] == "vroom-prod"
    assert steps[0][1]["label_selector"] == "app=dispatch-service"


# ── _parse_solver ──────────────────────────────────────────────────────────────

def test_parse_solver_clean_json():
    content = '{"root_cause":"no pods","confidence":"HIGH","remediation_tool":"scale_deployment","remediation_args":{"deployment":"ride-service","namespace":"vroom-dev"},"justification":"scaled to 0"}'
    result = rewoo_loop._parse_solver(content)
    assert result is not None
    assert result["confidence"] == "HIGH"
    assert result["remediation_tool"] == "scale_deployment"


def test_parse_solver_embedded_json():
    content = 'Here is my answer:\n{"root_cause":"crash","confidence":"MEDIUM","remediation_tool":"restart_deployment","remediation_args":{"deployment":"dispatch-service","namespace":"vroom-dev"},"justification":"OOMKilled"}\nDone.'
    result = rewoo_loop._parse_solver(content)
    assert result is not None
    assert result["remediation_tool"] == "restart_deployment"


def test_parse_solver_returns_none_on_garbage():
    assert rewoo_loop._parse_solver("This is not JSON at all.") is None
    assert rewoo_loop._parse_solver("") is None


# ── run_rewoo_loop ─────────────────────────────────────────────────────────────

def _make_sequential_llm(*responses):
    it = iter(responses)
    def _llm(messages, api_key):
        return next(it)
    return _llm


def test_loop_worker_calls_all_planned_tools():
    calls = []
    def fake_tool(name, args):
        calls.append(name)
        return f"obs:{name}"

    llm = _make_sequential_llm(
        '#E1 = get_pods(namespace="vroom-dev", label_selector="app=ride-service")\n#E2 = get_events(namespace="vroom-dev")',
        '{"root_cause":"scaled","confidence":"HIGH","remediation_tool":"scale_deployment","remediation_args":{"deployment":"ride-service","namespace":"vroom-dev"},"justification":"no pods"}',
    )
    result = rewoo_loop.run_rewoo_loop(ALERT, fake_tool, "key", _llm=llm)
    assert "get_pods" in calls
    assert "get_events" in calls
    assert len(result["rewoo_steps"]) == 2


def test_loop_solver_scale_deployment():
    llm = _make_sequential_llm(
        '#E1 = get_pods(namespace="vroom-dev", label_selector="app=ride-service")',
        '{"root_cause":"no pods — scaled to 0","confidence":"HIGH","remediation_tool":"scale_deployment","remediation_args":{"deployment":"ride-service","namespace":"vroom-dev"},"justification":"replicas=0"}',
    )
    result = rewoo_loop.run_rewoo_loop(ALERT, lambda t, a: "No resources found.", "key", _llm=llm)
    assert result["confidence"] == "HIGH"
    assert result["remediation"]["tool"] == "scale_deployment"
    assert result["remediation"]["args"]["deployment"] == "ride-service"


def test_loop_solver_none_remediation():
    llm = _make_sequential_llm(
        '#E1 = get_pods(namespace="vroom-dev", label_selector="app=ride-service")',
        '{"root_cause":"Redis unreachable","confidence":"MEDIUM","remediation_tool":"none","remediation_args":{},"justification":"dependency failure"}',
    )
    result = rewoo_loop.run_rewoo_loop(ALERT, lambda t, a: "pods running", "key", _llm=llm)
    assert result["confidence"] == "MEDIUM"
    assert result["remediation"] is None


def test_loop_solver_fallback_on_bad_json():
    llm = _make_sequential_llm(
        '#E1 = get_pods(namespace="vroom-dev", label_selector="app=ride-service")',
        "I cannot determine the issue from the evidence provided.",
    )
    result = rewoo_loop.run_rewoo_loop(ALERT, lambda t, a: "obs", "key", _llm=llm)
    assert result["confidence"] == "LOW"
    assert result["remediation"] is None


def test_loop_planner_fallback_on_empty_response():
    calls = []
    def fake_tool(name, args):
        calls.append(name)
        return "obs"

    llm = _make_sequential_llm(
        "",
        '{"root_cause":"x","confidence":"LOW","remediation_tool":"none","remediation_args":{},"justification":"y"}',
    )
    result = rewoo_loop.run_rewoo_loop(ALERT, fake_tool, "key", _llm=llm)
    assert "get_pods" in calls
    assert result["rewoo_steps"] != []


def test_loop_tool_error_does_not_crash_loop():
    def failing_tool(name, args):
        raise RuntimeError("executor unreachable")

    llm = _make_sequential_llm(
        '#E1 = get_pods(namespace="vroom-dev", label_selector="app=ride-service")',
        '{"root_cause":"unknown","confidence":"LOW","remediation_tool":"none","remediation_args":{},"justification":"tool failed"}',
    )
    result = rewoo_loop.run_rewoo_loop(ALERT, failing_tool, "key", _llm=llm)
    assert result["rewoo_steps"][0]["observation"].startswith("[tool error:")
    assert result["confidence"] == "LOW"


# ── _build_solver_prompt ───────────────────────────────────────────────────────

def test_solver_prompt_contains_three_steps():
    prompt = rewoo_loop._build_solver_prompt(
        "HighErrorRate", "ride-service", "vroom-dev",
        "rps=0", "#E1 (get_pods): NAME READY STATUS\nride-service 0/1 CrashLoopBackOff"
    )
    assert "STEP 1" in prompt
    assert "STEP 2" in prompt
    assert "STEP 3" in prompt
    assert "OBSERVE" in prompt
    assert "ROOT CAUSE" in prompt


def test_solver_prompt_criteria_cover_config_error():
    prompt = rewoo_loop._build_solver_prompt(
        "HighErrorRate", "ride-service", "vroom-dev", "rps=0", ""
    )
    assert "config error" in prompt.lower()
    assert "none" in prompt
    assert "zero pods" in prompt.lower()
