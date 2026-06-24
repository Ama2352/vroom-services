"""Layer 2 integration tests — LLM_MOCK=true, real tool functions, real parsing."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

try:
    import fakeredis
except ImportError:
    pytest.skip("fakeredis not installed", allow_module_level=True)

import rewoo_loop
from conftest import kubectl_scale_responses, kubectl_crashloop_responses

ALERT = {
    "alert_name":     "HighErrorRate",
    "service":        "ride-service",
    "namespace":      "vroom-dev",
    "bundle":         "rps=0 err=0% traces_errored=0",
    "memory_context": "",
}


def test_scale_to_zero_full_pipeline(monkeypatch):
    """Planner mock generates get_pods+get_events; Worker calls real tool fn;
    Solver mock picks scale_deployment."""
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.setenv("LLM_MOCK_SCENARIO", "scale_to_zero")

    result = rewoo_loop.run_rewoo_loop(ALERT, kubectl_scale_responses, "")

    assert result["confidence"] == "HIGH"
    assert result["remediation"] is not None
    assert result["remediation"]["tool"] == "scale_deployment"
    assert result["remediation"]["args"]["deployment"] == "ride-service"
    assert result["remediation"]["args"]["namespace"] == "vroom-dev"
    assert any("get_pods" in s["action"] for s in result["rewoo_steps"])
    assert any("No resources found" in s["observation"] for s in result["rewoo_steps"])


def test_crashloop_full_pipeline(monkeypatch):
    """Planner mock generates get_pods+get_logs; Solver mock picks restart_deployment."""
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.setenv("LLM_MOCK_SCENARIO", "crashloop")

    result = rewoo_loop.run_rewoo_loop(ALERT, kubectl_crashloop_responses, "")

    assert result["confidence"] == "HIGH"
    assert result["remediation"]["tool"] == "restart_deployment"
    assert any("get_pods" in s["action"] for s in result["rewoo_steps"])
    assert any("CrashLoopBackOff" in s["observation"] for s in result["rewoo_steps"])


def test_memory_prefetch_in_planner_prompt(monkeypatch):
    """Memory context passed in alert appears in the Planner prompt."""
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.setenv("LLM_MOCK_SCENARIO", "scale_to_zero")

    alert_with_memory = {
        **ALERT,
        "memory_context": (
            "Past incidents:\n"
            "[1] HighErrorRate on ride-service → scaled to 0 → scale_deployment → resolved"
        ),
    }

    prompt = rewoo_loop._build_planner_prompt(
        alert_with_memory["alert_name"],
        alert_with_memory["service"],
        alert_with_memory["namespace"],
        alert_with_memory["bundle"],
        alert_with_memory["memory_context"],
    )
    assert "scaled to 0" in prompt
    assert "scale_deployment" in prompt

    result = rewoo_loop.run_rewoo_loop(alert_with_memory, kubectl_scale_responses, "")
    assert result["confidence"] == "HIGH"


def test_no_real_llm_calls_in_mock_mode(monkeypatch):
    """LLM_MOCK=true must make zero HTTP requests to Groq or OpenRouter."""
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.setenv("LLM_MOCK_SCENARIO", "scale_to_zero")

    http_calls = []
    original_post = rewoo_loop.http_requests.post

    def tracking_post(url, **kwargs):
        http_calls.append(url)
        return original_post(url, **kwargs)

    monkeypatch.setattr(rewoo_loop.http_requests, "post", tracking_post)

    rewoo_loop.run_rewoo_loop(ALERT, kubectl_scale_responses, "fake-or-key",
                               groq_key="fake-groq-key")

    llm_calls = [u for u in http_calls
                 if "openrouter" in u or "groq" in u]
    assert llm_calls == [], f"Expected zero LLM API calls, got: {llm_calls}"


def test_mock_scenario_switching(monkeypatch):
    """Changing LLM_MOCK_SCENARIO between calls produces different remediation_tool."""
    monkeypatch.setenv("LLM_MOCK", "true")

    monkeypatch.setenv("LLM_MOCK_SCENARIO", "scale_to_zero")
    result1 = rewoo_loop.run_rewoo_loop(ALERT, kubectl_scale_responses, "")
    assert result1["remediation"]["tool"] == "scale_deployment"

    monkeypatch.setenv("LLM_MOCK_SCENARIO", "crashloop")
    result2 = rewoo_loop.run_rewoo_loop(ALERT, kubectl_crashloop_responses, "")
    assert result2["remediation"]["tool"] == "restart_deployment"
