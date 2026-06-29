import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from interpreter import (
    interpret, _parse_output, _fallback, _build_prompt, REQUIRED_KEYS
)

SAMPLE_FACTS = {
    "pods_available": 0, "pods_desired": 1,
    "waiting_reason": "CrashLoopBackOff", "restarts": 5,
    "log_error": "dial tcp postgres-primary:5432: i/o timeout",
    "event_reason": "BackOff", "event_message": "container failed to start",
    "event_object": "ride-abc123",
}

VALID_JSON = ('{"root_cause":"postgres unreachable",'
              '"dev_action":"check postgres pod",'
              '"kubectl_hint":"kubectl get pods -n platform"}')


class TestParseOutput:
    def test_valid_json_accepted(self):
        result = _parse_output(VALID_JSON)
        assert result["root_cause"]   == "postgres unreachable"
        assert result["dev_action"]   == "check postgres pod"
        assert result["kubectl_hint"] == "kubectl get pods -n platform"

    def test_json_inside_markdown_fence_extracted(self):
        raw = f"```json\n{VALID_JSON}\n```"
        assert _parse_output(raw) is not None
        assert _parse_output(raw)["root_cause"] == "postgres unreachable"

    def test_json_with_preamble_extracted(self):
        raw = f"Here is the diagnosis:\n{VALID_JSON}"
        assert _parse_output(raw) is not None

    def test_missing_key_returns_none(self):
        assert _parse_output('{"root_cause":"X","dev_action":"Y"}') is None

    def test_empty_string_returns_none(self):
        assert _parse_output("") is None

    def test_empty_value_returns_none(self):
        assert _parse_output('{"root_cause":"","dev_action":"Y","kubectl_hint":"Z"}') is None

    def test_non_dict_returns_none(self):
        assert _parse_output("[1,2,3]") is None

    def test_plain_text_returns_none(self):
        assert _parse_output("I cannot determine the root cause.") is None


class TestFallback:
    def test_uses_waiting_reason_in_root_cause(self):
        result = _fallback("ride", "vroom-dev", SAMPLE_FACTS)
        assert "CrashLoopBackOff" in result["root_cause"]

    def test_uses_log_error_in_root_cause(self):
        result = _fallback("ride", "vroom-dev", SAMPLE_FACTS)
        assert "i/o timeout" in result["root_cause"]

    def test_kubectl_hint_contains_namespace(self):
        assert "vroom-dev" in _fallback("ride", "vroom-dev", SAMPLE_FACTS)["kubectl_hint"]

    def test_kubectl_hint_contains_service(self):
        assert "ride" in _fallback("ride", "vroom-dev", SAMPLE_FACTS)["kubectl_hint"]

    def test_all_required_keys_present(self):
        assert REQUIRED_KEYS.issubset(_fallback("ride", "vroom-dev", SAMPLE_FACTS).keys())

    def test_handles_completely_empty_facts(self):
        empty = {k: ("" if isinstance(v, str) else 0) for k, v in SAMPLE_FACTS.items()}
        result = _fallback("ride", "vroom-dev", empty)
        assert result["root_cause"]
        assert result["dev_action"]
        assert result["kubectl_hint"]


class TestBuildPrompt:
    def test_includes_pod_count(self):
        prompt = _build_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "")
        assert "0 running / 1 desired" in prompt

    def test_includes_waiting_reason(self):
        prompt = _build_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "")
        assert "CrashLoopBackOff" in prompt

    def test_includes_log_error(self):
        prompt = _build_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "")
        assert "i/o timeout" in prompt

    def test_omits_empty_optional_fields(self):
        empty = {k: ("" if isinstance(v, str) else 0) for k, v in SAMPLE_FACTS.items()}
        prompt = _build_prompt("Alert", "ride", "vroom-dev", empty, "", "")
        assert "Container state:" not in prompt
        assert "Last error log:"  not in prompt
        assert "Last K8s event:"  not in prompt

    def test_includes_bundle_when_present(self):
        prompt = _build_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "rps=0.0 err=0.00%", "")
        assert "rps=0.0" in prompt

    def test_includes_memory_context_when_present(self):
        prompt = _build_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "",
                               "[1] past incident → root cause: postgres unreachable")
        assert "past incident" in prompt

    def test_ends_with_json_instruction(self):
        prompt = _build_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "")
        assert "root_cause" in prompt
        assert "kubectl_hint" in prompt


class TestInterpret:
    def test_valid_llm_output_returned(self):
        result = interpret(
            "KubePodNotReady", "ride", "vroom-dev",
            SAMPLE_FACTS, "", "", [],
            _llm=lambda msgs, key: VALID_JSON,
        )
        assert result["root_cause"]   == "postgres unreachable"
        assert result["kubectl_hint"] == "kubectl get pods -n platform"

    def test_invalid_llm_output_triggers_fallback(self):
        result = interpret(
            "KubePodNotReady", "ride", "vroom-dev",
            SAMPLE_FACTS, "", "", [],
            _llm=lambda msgs, key: "I cannot determine the issue.",
        )
        assert REQUIRED_KEYS.issubset(result.keys())
        assert result["root_cause"]

    def test_llm_exception_triggers_fallback(self):
        def bad(msgs, key):
            raise RuntimeError("API unavailable")
        result = interpret(
            "KubePodNotReady", "ride", "vroom-dev",
            SAMPLE_FACTS, "", "", [], _llm=bad,
        )
        assert REQUIRED_KEYS.issubset(result.keys())

    def test_all_required_keys_always_present(self):
        result = interpret(
            "KubePodNotReady", "ride", "vroom-dev",
            SAMPLE_FACTS, "", "", [],
            _llm=lambda msgs, key: VALID_JSON,
        )
        assert REQUIRED_KEYS.issubset(result.keys())

    def test_values_are_non_empty_strings(self):
        result = interpret(
            "KubePodNotReady", "ride", "vroom-dev",
            SAMPLE_FACTS, "", "", [],
            _llm=lambda msgs, key: VALID_JSON,
        )
        for k in REQUIRED_KEYS:
            assert isinstance(result[k], str) and result[k].strip()
