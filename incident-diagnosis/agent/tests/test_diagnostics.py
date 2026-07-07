import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from unittest.mock import patch, MagicMock
from diagnostics import (collect_diagnostics, format_evidence,
                          collect_change_evidence, resolve_dependency)


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


def _rs(created, image, env):
    return {
        "metadata": {"creationTimestamp": created},
        "spec": {"template": {"spec": {"containers": [
            {"image": image, "env": [{"name": k, "value": v} for k, v in env.items()]}
        ]}}},
    }


class TestCollectChangeEvidence:
    @patch("diagnostics.http_requests.get")
    def test_returns_none_when_fewer_than_2_replicasets(self, mock_get):
        mock_get.return_value = MagicMock(ok=True, json=lambda: {"items": [
            _rs("2026-07-07T01:00:00Z", "img:v1", {}),
        ]})
        assert collect_change_evidence("ride", "vroom-dev") is None

    @patch("diagnostics.http_requests.get")
    def test_returns_none_when_no_diff(self, mock_get):
        same = _rs("2026-07-07T01:00:00Z", "img:v1", {"REDIS_ADDR": "redis:6379"})
        mock_get.return_value = MagicMock(ok=True, json=lambda: {"items": [
            same, {**same, "metadata": {"creationTimestamp": "2026-07-07T02:00:00Z"}},
        ]})
        assert collect_change_evidence("ride", "vroom-dev") is None

    @patch("diagnostics.http_requests.get")
    def test_detects_env_change(self, mock_get):
        mock_get.return_value = MagicMock(ok=True, json=lambda: {"items": [
            _rs("2026-07-07T01:00:00Z", "img:v1", {"REDIS_ADDR": "redis.platform.svc.cluster.local:6379"}),
            _rs("2026-07-07T02:00:00Z", "img:v1", {"REDIS_ADDR": "bad-host:6379"}),
        ]})
        result = collect_change_evidence("ride", "vroom-dev")
        assert result["env_changed"] is True
        assert result["image_changed"] is False
        assert result["env_diff"] == [{
            "key": "REDIS_ADDR",
            "old_value": "redis.platform.svc.cluster.local:6379",
            "new_value": "bad-host:6379",
        }]
        assert result["changed_at"] == "2026-07-07T02:00:00Z"

    @patch("diagnostics.http_requests.get")
    def test_detects_image_change(self, mock_get):
        mock_get.return_value = MagicMock(ok=True, json=lambda: {"items": [
            _rs("2026-07-07T01:00:00Z", "ghcr.io/x/ride:build.77-abc1234", {}),
            _rs("2026-07-07T02:00:00Z", "ghcr.io/x/ride:build.78-621d9c3", {}),
        ]})
        result = collect_change_evidence("ride", "vroom-dev")
        assert result["image_changed"] is True
        assert result["old_image"] == "ghcr.io/x/ride:build.77-abc1234"
        assert result["new_image"] == "ghcr.io/x/ride:build.78-621d9c3"
        assert result["env_changed"] is False

    @patch("diagnostics.http_requests.get")
    def test_uses_newest_two_regardless_of_response_order(self, mock_get):
        # Route returns oldest-first; function must not assume a particular order.
        mock_get.return_value = MagicMock(ok=True, json=lambda: {"items": [
            _rs("2026-07-07T00:00:00Z", "img:v0", {}),
            _rs("2026-07-07T01:00:00Z", "img:v1", {}),
            _rs("2026-07-07T02:00:00Z", "img:v2", {}),
        ]})
        result = collect_change_evidence("ride", "vroom-dev")
        assert result["old_image"] == "img:v1"
        assert result["new_image"] == "img:v2"

    @patch("diagnostics.http_requests.get")
    def test_returns_none_on_http_error(self, mock_get):
        mock_get.return_value = MagicMock(ok=False)
        assert collect_change_evidence("ride", "vroom-dev") is None

    @patch("diagnostics.http_requests.get")
    def test_returns_none_on_exception(self, mock_get):
        mock_get.side_effect = Exception("connection refused")
        assert collect_change_evidence("ride", "vroom-dev") is None


class TestResolveDependency:
    def test_returns_none_when_no_ip_in_text(self):
        assert resolve_dependency("dial tcp: lookup bad-host: no such host", "") is None

    @patch("diagnostics.http_requests.get")
    def test_returns_none_when_ip_present_but_unresolved(self, mock_get):
        def side(url, **kw):
            if "resolve-service" in url:
                return MagicMock(ok=True, json=lambda: {})
            return MagicMock(ok=False)
        mock_get.side_effect = side
        result = resolve_dependency("dial tcp 10.43.68.150:5432: connect: connection refused", "")
        assert result is None

    @patch("diagnostics.http_requests.get")
    def test_resolves_and_checks_health(self, mock_get):
        def side(url, **kw):
            if "resolve-service" in url:
                assert kw["params"]["ip"] == "10.43.68.150"
                return MagicMock(ok=True, json=lambda: {"namespace": "platform", "name": "postgres"})
            q = kw.get("params", {}).get("query", "")
            if "replicas_available" in q:
                return MagicMock(ok=True, json=lambda: {
                    "data": {"result": [{"value": ["t", "0"], "metric": {}}]}})
            if "spec_replicas" in q:
                return MagicMock(ok=True, json=lambda: {
                    "data": {"result": [{"value": ["t", "1"], "metric": {}}]}})
            return MagicMock(ok=True, json=lambda: {"data": {"result": []}})
        mock_get.side_effect = side
        result = resolve_dependency("dial tcp 10.43.68.150:5432: connect: connection refused", "")
        assert result == {
            "name": "postgres", "namespace": "platform",
            "pods_available": 0, "pods_desired": 1, "waiting_reason": "",
        }

    @patch("diagnostics.http_requests.get")
    def test_extracts_ip_from_event_message_when_log_error_empty(self, mock_get):
        captured = {}
        def side(url, **kw):
            if "resolve-service" in url:
                captured["ip"] = kw["params"]["ip"]
                return MagicMock(ok=True, json=lambda: {"namespace": "platform", "name": "postgres"})
            return MagicMock(ok=True, json=lambda: {"data": {"result": []}})
        mock_get.side_effect = side
        resolve_dependency("", "connect to 10.43.68.150:5432 failed")
        assert captured["ip"] == "10.43.68.150"

    @patch("diagnostics.http_requests.get")
    def test_returns_none_on_resolve_http_error(self, mock_get):
        mock_get.return_value = MagicMock(ok=False)
        result = resolve_dependency("dial tcp 10.43.68.150:5432: connect: connection refused", "")
        assert result is None

    @patch("diagnostics.http_requests.get")
    def test_returns_none_on_exception(self, mock_get):
        mock_get.side_effect = Exception("connection refused")
        result = resolve_dependency("dial tcp 10.43.68.150:5432: connect: connection refused", "")
        assert result is None


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
