import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from interpreter import (
    interpret, _parse_output, _fallback, _build_grounded_prompt,
    _quality_check, _build_refine_prompt, _is_grounded,
    GROUNDING_RULE, REQUIRED_KEYS,
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

_GENERIC_JSON = ('{"root_cause":"potential issue with container initialization",'
                 '"dev_action":"investigate manually using kubectl",'
                 '"kubectl_hint":"kubectl describe pod <pod_name> -n vroom-dev"}')

_SPECIFIC_JSON = ('{"root_cause":"CrashLoopBackOff — postgres unreachable (dial tcp :5432: i/o timeout)",'
                  '"dev_action":"check postgres pod status in the platform namespace",'
                  '"kubectl_hint":"kubectl logs ride-abc123 -n vroom-dev --previous"}')


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


class TestBuildGroundedPrompt:
    def test_grounding_constraint_in_prompt(self):
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", "")
        assert GROUNDING_RULE in prompt

    def test_pod_name_in_prompt_when_provided(self):
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", "ride-abc123")
        assert "Pod: ride-abc123" in prompt

    def test_pod_line_absent_when_empty(self):
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", "")
        assert "Pod:" not in prompt

    def test_includes_pod_count(self):
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", "")
        assert "0/1 running" in prompt

    def test_includes_waiting_reason(self):
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", "")
        assert "CrashLoopBackOff" in prompt

    def test_includes_log_error(self):
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", "")
        assert "i/o timeout" in prompt

    def test_omits_empty_optional_fields(self):
        empty = {k: ("" if isinstance(v, str) else 0) for k, v in SAMPLE_FACTS.items()}
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", empty, "", "", "")
        assert "Container state:" not in prompt
        assert "Last error log:"  not in prompt
        assert "Last K8s event:"  not in prompt

    def test_includes_bundle_when_present(self):
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "rps=0.0 err=0.00%", "", "")
        assert "rps=0.0" in prompt

    def test_includes_memory_context_when_present(self):
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "",
                                        "[1] past incident → root cause: postgres unreachable", "")
        assert "past incident" in prompt

    def test_includes_trusted_match_framing_when_context_present(self):
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "",
                                        "Known failure pattern: postgres unreachable", "")
        assert "Trusted match from the knowledge base" in prompt

    def test_includes_trusted_match_usage_instruction_when_context_present(self):
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "",
                                        "Known failure pattern: postgres unreachable", "")
        assert "use it as the basis for your root_cause" in prompt.lower()

    def test_omits_trusted_match_framing_when_no_context(self):
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", "")
        assert "Trusted match from the knowledge base" not in prompt

    def test_ends_with_json_instruction(self):
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", "")
        assert "root_cause" in prompt
        assert "kubectl_hint" in prompt

    def test_includes_template_diff_env_change(self):
        facts = {**SAMPLE_FACTS, "template_diff": {
            "image_changed": False, "old_image": "", "new_image": "",
            "env_changed": True,
            "env_diff": [{"key": "REDIS_ADDR", "old_value": "redis:6379", "new_value": "bad-host:6379"}],
            "changed_at": "2026-07-07T02:00:00Z",
        }}
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", facts, "", "", "")
        assert "REDIS_ADDR" in prompt
        assert "bad-host:6379" in prompt

    def test_includes_template_diff_image_change(self):
        facts = {**SAMPLE_FACTS, "template_diff": {
            "image_changed": True, "old_image": "img:v1", "new_image": "img:v2",
            "env_changed": False, "env_diff": [], "changed_at": "2026-07-07T02:00:00Z",
        }}
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", facts, "", "", "")
        assert "img:v1" in prompt
        assert "img:v2" in prompt

    def test_omits_template_diff_when_absent(self):
        facts = {**SAMPLE_FACTS, "template_diff": None}
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", facts, "", "", "")
        assert "Recent change" not in prompt

    def test_includes_dependency(self):
        facts = {**SAMPLE_FACTS, "dependency": {
            "name": "postgres", "namespace": "platform",
            "pods_available": 0, "pods_desired": 1, "waiting_reason": "",
        }}
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", facts, "", "", "")
        assert "postgres" in prompt
        assert "platform" in prompt

    def test_omits_dependency_when_absent(self):
        facts = {**SAMPLE_FACTS, "dependency": None}
        prompt = _build_grounded_prompt("Alert", "ride", "vroom-dev", facts, "", "", "")
        assert "Dependency" not in prompt


class TestQualityCheck:
    def _clean(self):
        return {
            "root_cause":   "CrashLoopBackOff — postgres unreachable (dial tcp :5432: i/o timeout)",
            "dev_action":   "check postgres pod status in the platform namespace",
            "kubectl_hint": "kubectl logs ride-abc123 -n vroom-dev --previous",
        }

    def test_flags_generic_root_cause(self):
        d = self._clean()
        d["root_cause"] = "potential issue with container initialization"
        r = _quality_check(d, SAMPLE_FACTS, "ride-abc123", "ride")
        assert r["passed"] is False
        assert any("root_cause" in issue for issue in r["issues"])

    def test_flags_placeholder_in_kubectl_hint(self):
        d = self._clean()
        d["kubectl_hint"] = "kubectl describe pod <pod_name> -n vroom-dev"
        r = _quality_check(d, SAMPLE_FACTS, "ride-abc123", "ride")
        assert r["passed"] is False
        assert any("ride-abc123" in issue for issue in r["issues"])

    def test_placeholder_uses_label_selector_when_pod_empty(self):
        d = self._clean()
        d["kubectl_hint"] = "kubectl describe pod <pod_name> -n vroom-dev"
        r = _quality_check(d, SAMPLE_FACTS, "", "ride")
        assert r["passed"] is False
        assert any("-l app=ride" in issue for issue in r["issues"])

    def test_flags_generic_dev_action(self):
        d = self._clean()
        d["dev_action"] = "investigate manually using kubectl"
        r = _quality_check(d, SAMPLE_FACTS, "ride-abc123", "ride")
        assert r["passed"] is False
        assert any("dev_action" in issue for issue in r["issues"])

    def test_passes_insufficient_evidence(self):
        d = self._clean()
        d["root_cause"] = "Insufficient evidence: need init container logs"
        r = _quality_check(d, SAMPLE_FACTS, "ride-abc123", "ride")
        assert r["passed"] is True
        assert r["low_confidence"] is True
        assert r["issues"] == []

    def test_insufficient_evidence_without_grounding_now_fails(self):
        # Regression for the live bad-host/connection-refused cases: a hedge that
        # shares no token with the evidence is no longer accepted as-is — it must
        # trigger self-refine to restate the specific observed symptom.
        d = self._clean()
        d["root_cause"] = "Insufficient evidence: need more information"
        r = _quality_check(d, SAMPLE_FACTS, "ride-abc123", "ride")
        assert r["passed"] is False
        assert r["low_confidence"] is True
        assert any("restate the specific observed symptom" in issue for issue in r["issues"])

    def test_insufficient_evidence_with_grounding_still_passes(self):
        d = self._clean()
        d["root_cause"] = "Insufficient evidence to confirm — observed: i/o timeout. Need init container logs."
        r = _quality_check(d, SAMPLE_FACTS, "ride-abc123", "ride")
        assert r["passed"] is True
        assert r["low_confidence"] is True
        assert r["issues"] == []

    def test_insufficient_evidence_with_placeholder_still_fails(self):
        # Bug regression: early return on "insufficient evidence" was skipping
        # the placeholder check, so <pod_name> would slip through undetected.
        d = self._clean()
        d["root_cause"]   = "Insufficient evidence: need previous container logs"
        d["kubectl_hint"] = "kubectl logs ride-<pod_name> -n vroom-dev --previous"
        r = _quality_check(d, SAMPLE_FACTS, "ride-abc123", "ride")
        assert r["passed"] is False
        assert r["low_confidence"] is True
        assert any("placeholder" in issue for issue in r["issues"])

    def test_passes_clean_output(self):
        r = _quality_check(self._clean(), SAMPLE_FACTS, "ride-abc123", "ride")
        assert r["passed"] is True
        assert r["low_confidence"] is False
        assert r["issues"] == []

    def test_flags_ungrounded_root_cause(self):
        d = self._clean()
        d["root_cause"] = "Redis eviction causing dropped requests"
        r = _quality_check(d, SAMPLE_FACTS, "ride-abc123", "ride")
        assert r["passed"] is False
        assert any("not grounded" in issue for issue in r["issues"])


class TestIsGrounded:
    def test_passes_when_no_free_text_evidence(self):
        empty = {**SAMPLE_FACTS, "log_error": "", "event_message": ""}
        assert _is_grounded("Redis eviction causing dropped requests", empty) is True

    def test_fails_on_disconnected_root_cause(self):
        assert _is_grounded("Redis eviction causing dropped requests", SAMPLE_FACTS) is False

    def test_passes_on_overlapping_root_cause(self):
        assert _is_grounded("Database connection timeout during startup", SAMPLE_FACTS) is True

    def test_grounded_via_template_diff_env_value(self):
        facts = {**SAMPLE_FACTS, "log_error": "", "event_message": "", "template_diff": {
            "image_changed": False, "old_image": "", "new_image": "",
            "env_changed": True,
            "env_diff": [{"key": "REDIS_ADDR", "old_value": "redis:6379", "new_value": "bad-host:6379"}],
            "changed_at": "2026-07-07T02:00:00Z",
        }}
        assert _is_grounded("REDIS_ADDR was changed to bad-host:6379", facts) is True

    def test_grounded_via_dependency_name(self):
        facts = {**SAMPLE_FACTS, "log_error": "", "event_message": "", "dependency": {
            "name": "postgres", "namespace": "platform",
            "pods_available": 0, "pods_desired": 1, "waiting_reason": "",
        }}
        assert _is_grounded("postgres is unreachable", facts) is True


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


class TestBuildRefinePrompt:
    def _original(self):
        return _build_grounded_prompt("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", "")

    def test_contains_original_prompt(self):
        original  = self._original()
        diagnosis = {"root_cause": "potential issue", "dev_action": "investigate manually",
                     "kubectl_hint": "kubectl get pods"}
        refine = _build_refine_prompt(original, diagnosis, ["root_cause is vague"])
        assert original in refine

    def test_contains_issue_list(self):
        original  = self._original()
        diagnosis = {"root_cause": "potential issue", "dev_action": "investigate manually",
                     "kubectl_hint": "kubectl get pods"}
        issues = ["root_cause uses vague language", "dev_action is too vague"]
        refine = _build_refine_prompt(original, diagnosis, issues)
        assert "root_cause uses vague language" in refine
        assert "dev_action is too vague" in refine

    def test_contains_previous_diagnosis(self):
        original  = self._original()
        diagnosis = {"root_cause": "potential issue", "dev_action": "investigate manually",
                     "kubectl_hint": "kubectl get pods"}
        refine = _build_refine_prompt(original, diagnosis, ["root_cause is vague"])
        assert "potential issue" in refine


class TestInterpretPipeline:
    def test_self_refine_triggered_when_quality_check_fails(self):
        from unittest.mock import Mock
        mock_llm = Mock(side_effect=[_GENERIC_JSON, _SPECIFIC_JSON])
        interpret("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", [],
                  pod="ride-abc123", _llm=mock_llm)
        assert mock_llm.call_count == 2

    def test_self_refine_not_triggered_when_quality_check_passes(self):
        from unittest.mock import Mock
        mock_llm = Mock(return_value=_SPECIFIC_JSON)
        interpret("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", [],
                  pod="ride-abc123", _llm=mock_llm)
        assert mock_llm.call_count == 1

    def test_refine_result_returned_when_successful(self):
        from unittest.mock import Mock
        mock_llm = Mock(side_effect=[_GENERIC_JSON, _SPECIFIC_JSON])
        result = interpret("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", [],
                           pod="ride-abc123", _llm=mock_llm)
        assert "postgres unreachable" in result["root_cause"]

    def test_phase1_returned_when_refine_parse_fails(self):
        from unittest.mock import Mock
        mock_llm = Mock(side_effect=[_GENERIC_JSON, "not valid json at all"])
        result = interpret("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", [],
                           pod="ride-abc123", _llm=mock_llm)
        assert mock_llm.call_count == 2
        assert "potential issue" in result["root_cause"]

    def test_end_to_end_two_calls(self):
        from unittest.mock import Mock
        mock_llm = Mock(side_effect=[_GENERIC_JSON, _SPECIFIC_JSON])
        result = interpret("KubePodNotReady", "ride", "vroom-dev",
                           SAMPLE_FACTS, "", "", [], pod="ride-abc123", _llm=mock_llm)
        assert mock_llm.call_count == 2
        assert "postgres unreachable" in result["root_cause"]
        assert result.get("low_confidence") is False

    def test_end_to_end_one_call(self):
        from unittest.mock import Mock
        mock_llm = Mock(return_value=_SPECIFIC_JSON)
        result = interpret("KubePodNotReady", "ride", "vroom-dev",
                           SAMPLE_FACTS, "", "", [], pod="ride-abc123", _llm=mock_llm)
        assert mock_llm.call_count == 1
        assert result.get("low_confidence") is False


class TestRefineTemperature:
    def test_refine_call_uses_higher_temperature(self):
        from unittest.mock import patch, Mock
        resp1 = Mock()
        resp1.raise_for_status = Mock()
        resp1.json.return_value = {"choices": [{"message": {"content": _GENERIC_JSON}}]}
        resp2 = Mock()
        resp2.raise_for_status = Mock()
        resp2.json.return_value = {"choices": [{"message": {"content": _SPECIFIC_JSON}}]}
        with patch("interpreter.http_requests.post", side_effect=[resp1, resp2]) as mock_post:
            interpret("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "",
                      [{"id": "test-model", "provider": "groq"}],
                      groq_key="fake-key", pod="ride-abc123")
        assert mock_post.call_count == 2
        first_temp  = mock_post.call_args_list[0].kwargs["json"]["temperature"]
        second_temp = mock_post.call_args_list[1].kwargs["json"]["temperature"]
        assert first_temp  == 0.1
        assert second_temp == 0.4


class TestStepLog:
    def test_step_log_present_when_phase1_passes(self):
        result = interpret("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", [],
                            pod="ride-abc123", _llm=lambda msgs, key: _SPECIFIC_JSON)
        names = [s["name"] for s in result["_step_log"]]
        assert names == ["llm_phase1", "quality_check"]

    def test_step_log_includes_refine_when_triggered(self):
        from unittest.mock import Mock
        mock_llm = Mock(side_effect=[_GENERIC_JSON, _SPECIFIC_JSON])
        result = interpret("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", [],
                            pod="ride-abc123", _llm=mock_llm)
        names = [s["name"] for s in result["_step_log"]]
        assert names == ["llm_phase1", "quality_check", "llm_refine"]

    def test_step_log_only_phase1_when_parse_fails(self):
        result = interpret("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", [],
                            _llm=lambda msgs, key: "not valid json at all")
        names = [s["name"] for s in result["_step_log"]]
        assert names == ["llm_phase1"]

    def test_step_entries_have_timing_and_metadata_fields(self):
        result = interpret("Alert", "ride", "vroom-dev", SAMPLE_FACTS, "", "", [],
                            pod="ride-abc123", _llm=lambda msgs, key: _SPECIFIC_JSON)
        for s in result["_step_log"]:
            assert s["type"] == "step"
            assert "started_at" in s and "finished_at" in s
            assert isinstance(s["duration_ms"], int)
            assert "metadata" in s
