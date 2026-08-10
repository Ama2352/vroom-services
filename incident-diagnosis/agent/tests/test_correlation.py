import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from correlation import collect_log_evidence, correlate_trace, derive_log_error

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
LOG_EVIDENCE = {"status": "found", "service": "dispatch-service", "namespace": "vroom-dev",
                "operation": "dispatch.consume.UNKNOWN_EVENT_TYPE_DEMO",
                "message": "unknown event type moved to DLQ", "trace_id": TRACE_ID}


def _loki_response(line):
    response = MagicMock(ok=True)
    response.json.return_value = {"data": {"result": [{"stream": {"service": "dispatch-service"},
                                                        "values": [["1775038510000000000", line]]}]}}
    return response


def _empty_loki_response():
    response = MagicMock(ok=True)
    response.json.return_value = {"data": {"result": []}}
    return response


def _tempo_response(service="dispatch-service", operation="dispatch.consume.UNKNOWN_EVENT_TYPE_DEMO", message="unknown event type moved to DLQ"):
    response = MagicMock(ok=True, status_code=200)
    response.json.return_value = {"batches": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
        "scopeSpans": [{"spans": [{"traceId": TRACE_ID, "spanId": "00f067aa0ba902b7", "name": operation,
            "status": {"code": "STATUS_CODE_ERROR"}, "events": [{"name": "exception", "attributes": [{"key": "exception.message", "value": {"stringValue": message}}]}]}]}]}]}
    return response


def test_collect_log_evidence_returns_trace_fields():
    line = json.dumps({"timestamp": "2026-08-04T10:15:10Z", "level": "error",
                       "service": "dispatch-service", "operation": "dispatch.consume.UNKNOWN_EVENT_TYPE_DEMO",
                       "message": "unknown event type moved to DLQ", "trace_id": TRACE_ID,
                       "span_id": "00f067aa0ba902b7", "event_id": "123-0"})
    with patch("requests.get", return_value=_loki_response(line)):
        result = collect_log_evidence("dispatch-service", "vroom-dev", 1775000000, 1775000900)
    assert result["status"] == "found"
    assert result["trace_id"] == TRACE_ID


def test_collect_log_evidence_selects_scoped_plain_startup_error_without_trace_id():
    line = "Redis not ready: dial tcp: lookup bad-host on 10.43.0.10:53: no such host"
    with patch("requests.get", side_effect=[_empty_loki_response(), _loki_response(line)]):
        result = collect_log_evidence("ride-service", "vroom-dev", 1775000000, 1775000900)

    assert result["status"] == "found"
    assert result["log_format"] == "plain"
    assert result["trace_id"] == ""
    assert "bad-host" in result["message"]


def test_collect_log_evidence_prefers_structured_record_over_plain_fallback():
    structured = json.dumps({
        "level": "ERROR", "service": "dispatch-service",
        "message": 'unknown event type "Trip.Requested.v2"', "trace_id": TRACE_ID,
    })
    with patch("requests.get", return_value=_loki_response(structured)):
        result = collect_log_evidence("dispatch-service", "vroom-dev", 1775000000, 1775000900)

    assert result["log_format"] == "structured"
    assert result["trace_id"] == TRACE_ID


def test_collect_log_evidence_accepts_uppercase_structured_error_level():
    line = json.dumps({"timestamp": "2026-08-05T09:31:54Z", "level": "ERROR",
                       "service": "dispatch-service", "operation": "dispatch.consume",
                       "message": "unknown event type", "trace_id": TRACE_ID})
    with patch("requests.get", return_value=_loki_response(line)) as get:
        result = collect_log_evidence("dispatch-service", "vroom-dev", 1775000000, 1775000900)
    assert result["status"] == "found"
    assert 'level=~"(?i)^error$"' in get.call_args.kwargs["params"]["query"]


def test_collect_log_evidence_normalizes_go_slog_msg_field():
    line = json.dumps({"time": "2026-08-06T10:34:31Z", "level": "ERROR",
                       "service": "dispatch-service", "operation": "dispatch.consume",
                       "msg": 'unknown event type "Trip.Requested.v2"', "trace_id": TRACE_ID,
                       "span_id": "00f067aa0ba902b7", "event_id": "1786012471819-0"})
    with patch("requests.get", return_value=_loki_response(line)):
        result = collect_log_evidence("dispatch-service", "vroom-dev", 1775038510, 1775039410)
    assert result["message"] == 'unknown event type "Trip.Requested.v2"'
    assert derive_log_error(result) == 'unknown event type "Trip.Requested.v2"'


def test_collect_log_evidence_selects_nearest_record_not_last_record_in_stream():
    near = json.dumps({"level": "ERROR", "service": "dispatch-service", "operation": "dispatch.consume",
                       "msg": "nearest failure", "trace_id": TRACE_ID})
    far = json.dumps({"level": "ERROR", "service": "dispatch-service", "operation": "dispatch.consume",
                      "msg": "far failure", "trace_id": "a" * 32})
    response = MagicMock(ok=True)
    response.json.return_value = {"data": {"result": [{"values": [
        ["1775038510000000000", near],
        ["1775038990000000000", far],
    ]}]}}
    with patch("requests.get", return_value=response):
        result = collect_log_evidence("dispatch-service", "vroom-dev", 1775038511, 1775039410)
    assert result["message"] == "nearest failure"
    assert result["trace_id"] == TRACE_ID


def test_collect_log_evidence_queries_a_pre_alert_lookback_window():
    start = 1775000000
    line = json.dumps({"level": "error", "service": "dispatch-service", "trace_id": TRACE_ID})
    with patch("requests.get", return_value=_loki_response(line)) as get:
        collect_log_evidence("dispatch-service", "vroom-dev", start, start + 900)
    assert get.call_args.kwargs["params"]["start"] == str((start - 120) * 1_000_000_000)


def test_correlate_trace_fetches_exact_log_trace_id():
    with patch("requests.get", return_value=_tempo_response()) as get:
        trace = correlate_trace(LOG_EVIDENCE)
    assert get.call_args.args[0].endswith(f"/api/traces/{TRACE_ID}")
    assert trace["status"] == "correlated"
    assert trace["error_service"] == "dispatch-service"
    assert f'"query": "{TRACE_ID}"' in trace["grafana_url"] or f'%22query%22%3A+%22{TRACE_ID}%22' in trace["grafana_url"]


def test_correlate_trace_returns_upstream_services_from_same_trace():
    response = _tempo_response()
    response.json.return_value["batches"].insert(0, {
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "ride-service"}}]},
        "scopeSpans": [{"spans": [{
            "traceId": TRACE_ID, "spanId": "1111111111111111", "name": "POST /api/v1/trips",
            "startTimeUnixNano": "1775038509000000000", "endTimeUnixNano": "1775038510000000000",
            "status": {"code": "STATUS_CODE_OK"}, "events": [],
        }]}],
    })
    with patch("requests.get", return_value=response):
        trace = correlate_trace(LOG_EVIDENCE)
    assert trace["status"] == "correlated"
    assert trace["involved_services"] == ["ride-service", "dispatch-service"]


def test_correlate_trace_reports_conflict_when_error_is_unrelated():
    with patch("requests.get", return_value=_tempo_response("user-service", "auth.request", "JWT expired")):
        trace = correlate_trace(LOG_EVIDENCE)
    assert trace["status"] == "conflict"


def test_correlate_trace_reports_no_trace_id():
    assert correlate_trace({"status": "found", "message": "error"})["status"] == "no_trace_id"


def test_legacy_log_error_is_derived_from_selected_structured_record():
    selected = {"status": "found", "message": "unknown event type Trip.Requested.v2", "trace_id": TRACE_ID}
    assert derive_log_error(selected) == "unknown event type Trip.Requested.v2"


def test_trace_outside_incident_window_is_conflicting(monkeypatch):
    monkeypatch.setattr("correlation.fetch_trace", lambda _: {
        "status": "fetched", "trace_id": TRACE_ID,
        "payload": {"batches": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "dispatch-service"}}]},
            "scopeSpans": [{"spans": [{"startTimeUnixNano": "1775038510000000000", "name": "dispatch.consume.UNKNOWN_EVENT_TYPE_DEMO",
                "status": {"code": "STATUS_CODE_ERROR"}, "events": []}]}]}]},
    })
    result = correlate_trace(LOG_EVIDENCE, 1775038500, 1775038501)
    assert result["status"] == "conflict"
    assert result["reason"] == "trace outside incident window"
