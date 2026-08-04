import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from correlation import collect_log_evidence, correlate_trace

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
LOG_EVIDENCE = {"status": "found", "service": "dispatch-service", "namespace": "vroom-dev",
                "operation": "dispatch.consume.UNKNOWN_EVENT_TYPE_DEMO",
                "message": "unknown event type moved to DLQ", "trace_id": TRACE_ID}


def _loki_response(line):
    response = MagicMock(ok=True)
    response.json.return_value = {"data": {"result": [{"stream": {"service": "dispatch-service"},
                                                        "values": [["1775038510000000000", line]]}]}}
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


def test_correlate_trace_fetches_exact_log_trace_id():
    with patch("requests.get", return_value=_tempo_response()) as get:
        trace = correlate_trace(LOG_EVIDENCE)
    assert get.call_args.args[0].endswith(f"/api/traces/{TRACE_ID}")
    assert trace["status"] == "correlated"
    assert trace["error_service"] == "dispatch-service"


def test_correlate_trace_reports_conflict_when_error_is_unrelated():
    with patch("requests.get", return_value=_tempo_response("user-service", "auth.request", "JWT expired")):
        trace = correlate_trace(LOG_EVIDENCE)
    assert trace["status"] == "conflict"


def test_correlate_trace_reports_no_trace_id():
    assert correlate_trace({"status": "found", "message": "error"})["status"] == "no_trace_id"
