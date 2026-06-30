import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from unittest.mock import patch, MagicMock
from diagnostics import collect_diagnostics, format_evidence


def _prom_scalar(value):
    return MagicMock(ok=True, json=lambda: {
        "data": {"result": [{"value": ["t", str(value)], "metric": {}}]}
    })


def _prom_label(label_key, label_val):
    return MagicMock(ok=True, json=lambda: {
        "data": {"result": [{"value": ["t", "1"], "metric": {label_key: label_val}}]}
    })


def _prom_empty():
    return MagicMock(ok=True, json=lambda: {"data": {"result": []}})


def _loki_ok(log_line):
    return MagicMock(ok=True, json=lambda: {
        "data": {"result": [{"values": [["ts", log_line]]}]}
    })


def _events_ok(events):
    return MagicMock(ok=True, json=lambda: {"events": events})


def _fail():
    return MagicMock(ok=False)


class TestCollectDiagnostics:
    @patch("diagnostics.http_requests.get")
    def test_returns_all_expected_keys(self, mock_get):
        mock_get.return_value = _fail()
        result = collect_diagnostics("ride", "vroom-dev")
        assert set(result.keys()) == {
            "pods_available", "pods_desired", "waiting_reason", "last_terminated_reason",
            "restarts", "init_waiting_reason", "init_last_terminated_reason", "init_restarts",
            "log_error", "event_reason", "event_message", "event_object",
        }

    @patch("diagnostics.http_requests.get")
    def test_all_sources_down_returns_safe_defaults(self, mock_get):
        mock_get.return_value = _fail()
        result = collect_diagnostics("ride", "vroom-dev")
        assert result["pods_available"] == 0
        assert result["pods_desired"]   == 0
        assert result["waiting_reason"] == ""
        assert result["restarts"]       == 0
        assert result["log_error"]      == ""
        assert result["event_reason"]   == ""

    @patch("diagnostics.http_requests.get")
    def test_pods_available_and_desired_extracted(self, mock_get):
        def side(url, **kw):
            q = kw.get("params", {}).get("query", "")
            if "replicas_available" in q:
                return _prom_scalar(0)
            if "spec_replicas" in q:
                return _prom_scalar(1)
            return _fail()
        mock_get.side_effect = side
        result = collect_diagnostics("ride", "vroom-dev")
        assert result["pods_available"] == 0
        assert result["pods_desired"]   == 1

    @patch("diagnostics.http_requests.get")
    def test_waiting_reason_extracted_from_active_series(self, mock_get):
        def side(url, **kw):
            q = kw.get("params", {}).get("query", "")
            if "waiting_reason" in q:
                return _prom_label("reason", "CrashLoopBackOff")
            if "restarts_total" in q:
                return _prom_scalar(5)
            return _prom_scalar(0)
        mock_get.side_effect = side
        result = collect_diagnostics("ride", "vroom-dev")
        assert result["waiting_reason"] == "CrashLoopBackOff"
        assert result["restarts"]       == 5
        assert result["pods_available"] == 0
        assert result["pods_desired"]   == 0

    @patch("diagnostics.http_requests.get")
    def test_waiting_reason_empty_when_no_active_series(self, mock_get):
        mock_get.return_value = _prom_empty()
        result = collect_diagnostics("ride", "vroom-dev")
        assert result["waiting_reason"] == ""

    @patch("diagnostics.http_requests.get")
    def test_loki_latest_error_extracted(self, mock_get):
        log_line = "dial tcp postgres-primary:5432: i/o timeout"
        def side(url, **kw):
            if "query_range" in url or "loki" in url:
                return _loki_ok(log_line)
            return _fail()
        mock_get.side_effect = side
        result = collect_diagnostics("ride", "vroom-dev")
        assert result["log_error"] == log_line

    @patch("diagnostics.http_requests.get")
    def test_loki_query_uses_broad_pattern_not_exact_error(self, mock_get):
        # Regression: |= "error" (case-sensitive) misses "failed", "refused", etc.
        # Query must use a regex pattern covering common failure keywords.
        captured = {}
        def side(url, **kw):
            if "query_range" in url or "loki" in url:
                captured["query"] = kw.get("params", {}).get("query", "")
                return _loki_ok("redis: failed to dial after 5 attempts")
            return _fail()
        mock_get.side_effect = side
        collect_diagnostics("ride", "vroom-dev")
        q = captured.get("query", "")
        assert "|= \"error\"" not in q, "exact |= match misses 'failed', 'refused', etc."
        assert "failed" in q
        assert "refused" in q

    @patch("diagnostics.http_requests.get")
    def test_loki_log_truncated_at_200_chars(self, mock_get):
        long_log = "x" * 300
        def side(url, **kw):
            if "query_range" in url or "loki" in url:
                return _loki_ok(long_log)
            return _fail()
        mock_get.side_effect = side
        result = collect_diagnostics("ride", "vroom-dev")
        assert len(result["log_error"]) == 200

    @patch("diagnostics.http_requests.get")
    def test_k8s_event_fields_extracted(self, mock_get):
        event = {"reason": "BackOff", "message": "container failed",
                 "object": "ride-abc", "last_seen": "2026-06-29T10:00:00Z"}
        def side(url, **kw):
            if "events-json" in url:
                return _events_ok([event])
            return _fail()
        mock_get.side_effect = side
        result = collect_diagnostics("ride", "vroom-dev")
        assert result["event_reason"]  == "BackOff"
        assert result["event_object"]  == "ride-abc"
        assert result["event_message"] == "container failed"

    @patch("diagnostics.http_requests.get")
    def test_events_empty_returns_empty_fields(self, mock_get):
        def side(url, **kw):
            if "events-json" in url:
                return _events_ok([])
            return _fail()
        mock_get.side_effect = side
        result = collect_diagnostics("ride", "vroom-dev")
        assert result["event_reason"]  == ""
        assert result["event_message"] == ""
        assert result["event_object"]  == ""


class TestFormatEvidence:
    BASE = {
        "pods_available": 0, "pods_desired": 1,
        "waiting_reason": "", "restarts": 0,
        "log_error": "", "event_reason": "", "event_message": "", "event_object": "",
    }

    def test_always_includes_pod_line(self):
        assert "Pods: 0/1 running" in format_evidence(self.BASE)

    def test_waiting_reason_in_pod_line(self):
        facts = {**self.BASE, "waiting_reason": "CrashLoopBackOff", "restarts": 5}
        ev = format_evidence(facts)
        assert "CrashLoopBackOff" in ev
        assert "5 restarts" in ev

    def test_restarts_shown_without_waiting_reason(self):
        facts = {**self.BASE, "restarts": 3}
        assert "3 restarts" in format_evidence(facts)

    def test_log_error_included(self):
        facts = {**self.BASE, "log_error": "connection refused"}
        assert "Error: connection refused" in format_evidence(facts)

    def test_log_error_omitted_when_empty(self):
        assert "Error:" not in format_evidence(self.BASE)

    def test_event_included_with_all_parts(self):
        facts = {**self.BASE, "event_reason": "BackOff",
                 "event_object": "ride-abc", "event_message": "container failed"}
        ev = format_evidence(facts)
        assert "BackOff"          in ev
        assert "ride-abc"         in ev
        assert "container failed" in ev

    def test_event_omitted_when_reason_empty(self):
        assert "Event:" not in format_evidence(self.BASE)

    def test_at_most_three_lines(self):
        facts = {
            "pods_available": 0, "pods_desired": 1,
            "waiting_reason": "CrashLoopBackOff", "restarts": 3,
            "log_error": "connection refused",
            "event_reason": "BackOff", "event_message": "failed", "event_object": "ride-abc",
        }
        lines = [l for l in format_evidence(facts).split("\n") if l.strip()]
        assert len(lines) <= 3

    def test_fallback_message_when_all_empty(self):
        empty = {**self.BASE, "pods_available": 0, "pods_desired": 0}
        result = format_evidence(empty)
        assert result  # non-empty string
