import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import react_loop
from unittest.mock import patch, MagicMock


ALERT = {
    "alert_name": "HighErrorRate",
    "service": "ride-service",
    "namespace": "vroom-dev",
    "bundle": "rps=12.4 err=8.3% p99=1.2s loki_errors=47",
}


def test_parse_action_extracts_tool_and_args():
    text = "Thought: check pods\nAction: get_pods(namespace=vroom-dev)"
    name, args = react_loop._parse_action(text)
    assert name == "get_pods"
    assert args == {"namespace": "vroom-dev"}


def test_parse_action_returns_none_on_malformed():
    name, args = react_loop._parse_action("I will check things eventually")
    assert name is None
    assert args is None


def test_parse_final_extracts_json():
    text = 'Final Answer: {"root_cause":"disk full","confidence":"HIGH","remediation":null}'
    result = react_loop._parse_final(text)
    assert result["root_cause"] == "disk full"
    assert result["confidence"] == "HIGH"
    assert result["remediation"] is None


def test_parse_final_strips_markdown_fences():
    text = 'Final Answer: ```json\n{"root_cause":"x","confidence":"LOW","remediation":null}\n```'
    result = react_loop._parse_final(text)
    assert result["root_cause"] == "x"


def test_parse_final_returns_none_on_missing():
    assert react_loop._parse_final("just some text") is None


def test_loop_returns_final_answer_on_first_step():
    llm_response = 'Final Answer: {"root_cause":"stale cursor","confidence":"HIGH","remediation":{"tool":"restart_deployment","args":{"deployment":"dispatch-service","namespace":"vroom-dev"},"justification":"safe"}}'

    def fake_llm(messages, api_key):
        return llm_response

    result = react_loop.run_react_loop(ALERT, lambda t, a: "ok", "fake-key", _llm=fake_llm)
    assert result["root_cause"] == "stale cursor"
    assert result["confidence"] == "HIGH"
    assert result["investigation_steps"] == []


def test_loop_calls_tool_and_continues():
    responses = [
        "Thought: check pods\nAction: get_pods(namespace=vroom-dev)",
        'Final Answer: {"root_cause":"crash","confidence":"MEDIUM","remediation":null}',
    ]
    calls = iter(responses)

    def fake_llm(messages, api_key):
        return next(calls)

    tool_calls = []
    def fake_tool(name, args):
        tool_calls.append(name)
        return "NAME READY STATUS\npod-abc 1/1 Running"

    result = react_loop.run_react_loop(ALERT, fake_tool, "fake-key", _llm=fake_llm)
    assert "get_pods" in tool_calls
    assert result["confidence"] == "MEDIUM"
    assert len(result["investigation_steps"]) == 1


def test_loop_returns_low_confidence_fallback_after_max_steps():
    def fake_llm(messages, api_key):
        return "Thought: still thinking"  # never outputs Final Answer

    result = react_loop.run_react_loop(ALERT, lambda t, a: "data", "fake-key", _llm=fake_llm)
    assert result["confidence"] == "LOW"
    assert result["remediation"] is None


def test_system_prompt_includes_search_memory():
    captured = []

    def fake_llm(messages, api_key):
        captured.extend(messages)
        return 'Final Answer: {"root_cause":"x","confidence":"LOW","remediation":null}'

    react_loop.run_react_loop(ALERT, lambda t, a: "", "fake-key", _llm=fake_llm)
    system_msg = next(m["content"] for m in captured if m["role"] == "system")
    assert "search_memory" in system_msg


# ── Function-calling primary path ─────────────────────────────────────────────

def _make_tool_call_msg(tool_name, arguments_dict, content=""):
    """Simulate what _default_llm returns when the model uses function calling."""
    import json
    return {
        "content": content,
        "tool_calls": [{
            "id": "call_test",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(arguments_dict),
            }
        }]
    }


def test_loop_handles_tool_call_response():
    """Primary path: model returns tool_calls → tool is invoked, obs appended."""
    responses = [
        _make_tool_call_msg("get_pods", {"namespace": "vroom-dev", "label_selector": "app=ride-service"}),
        _make_tool_call_msg("final_answer", {
            "root_cause": "pods missing", "confidence": "HIGH",
            "remediation_tool": "scale_deployment",
            "remediation_args": {"deployment": "ride-service", "namespace": "vroom-dev", "replicas": 1},
            "justification": "scaled to 0",
        }),
    ]
    idx = [0]

    def fake_llm_dict(msgs, key):
        r = responses[idx[0]]
        idx[0] += 1
        return r

    tool_calls = []
    def fake_tool(name, args):
        tool_calls.append(name)
        return "dispatch-service-abc 1/1 Running"   # ride-service absent → sets has_no_target_pods

    result = react_loop.run_react_loop(ALERT, fake_tool, "fake-key", _llm=fake_llm_dict)
    assert "get_pods" in tool_calls
    assert result["confidence"] == "HIGH"
    assert result["remediation"]["tool"] == "scale_deployment"
    assert len(result["investigation_steps"]) == 1


def test_loop_handles_final_answer_tool_call():
    """Primary path: final_answer tool call terminates loop immediately."""
    def fake_llm_dict(msgs, key):
        return _make_tool_call_msg("final_answer", {
            "root_cause": "scaled to 0",
            "confidence": "HIGH",
            "remediation_tool": "scale_deployment",
            "remediation_args": {"deployment": "ride-service", "namespace": "vroom-dev"},
            "justification": "test",
        })

    result = react_loop.run_react_loop(ALERT, lambda t, a: "obs", "fake-key", _llm=fake_llm_dict)
    assert result["root_cause"] == "scaled to 0"
    assert result["remediation"]["tool"] == "scale_deployment"
    assert result["investigation_steps"] == []   # terminated before any tool call


def test_loop_text_fallback_when_no_tool_calls():
    """Fallback path: model returns content with no tool_calls → text parsing used."""
    responses = [
        {"content": "Thought: check\nAction: get_pods(namespace=vroom-dev)", "tool_calls": []},
        {"content": 'Final Answer: {"root_cause":"crash","confidence":"MEDIUM","remediation":null}', "tool_calls": []},
    ]
    idx = [0]

    def fake_llm_dict(msgs, key):
        r = responses[idx[0]]
        idx[0] += 1
        return r

    result = react_loop.run_react_loop(ALERT, lambda t, a: "pod data", "fake-key", _llm=fake_llm_dict)
    assert result["confidence"] == "MEDIUM"
    assert result["remediation"] is None
    assert len(result["investigation_steps"]) == 1
