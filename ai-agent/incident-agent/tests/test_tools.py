from unittest.mock import patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tools


def _ok(stdout):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"stdout": stdout, "returncode": 0}
    return r


def _err(status):
    r = MagicMock()
    r.status_code = status
    return r


def test_get_pods_calls_correct_endpoint():
    with patch("requests.get", return_value=_ok("pod-abc Running")) as mock_get:
        result = tools.call_tool("get_pods", {"namespace": "vroom-dev"})
    mock_get.assert_called_once()
    assert "/tools/pods" in mock_get.call_args[0][0]
    assert result == "pod-abc Running"


def test_get_logs_passes_params():
    with patch("requests.get", return_value=_ok("log line")) as mock_get:
        tools.call_tool("get_logs", {"service": "ride-service", "namespace": "vroom-dev", "tail": "50"})
    params = mock_get.call_args[1]["params"]
    assert params["service"] == "ride-service"
    assert params["tail"] == "50"


def test_unknown_tool_returns_error_string():
    result = tools.call_tool("delete_everything", {})
    assert "[unknown tool" in result


def test_executor_http_error_returns_error_string():
    with patch("requests.get", return_value=_err(400)):
        result = tools.call_tool("get_pods", {"namespace": "vroom-dev"})
    assert "[tool error" in result


def test_get_traces_uses_short_timeout():
    with patch("requests.get", return_value=_ok("trace abc")) as mock_get:
        tools.call_tool("get_traces", {"service": "ride-service", "error_only": "true"})
    assert mock_get.call_args[1]["timeout"] == 15


# ── _process_logs filter + dedup ──────────────────────────────────────────────

from tools import _process_logs


def test_excludes_healthz_200_but_keeps_healthz_500():
    logs = (
        "2026-06-29T10:00:01Z GET /healthz | 200 | 1ms\n"
        "2026-06-29T10:00:02Z GET /healthz | 503 | 2ms\n"
        "2026-06-29T10:00:03Z level=error msg=\"connection refused\"\n"
    )
    result = _process_logs(logs)
    assert "| 200 |" not in result
    assert "| 503 |" in result
    assert "connection refused" in result


def test_prefers_error_level_lines():
    logs = (
        "2026-06-29T10:00:01Z level=info msg=\"request received\"\n"
        "2026-06-29T10:00:02Z level=error msg=\"dial tcp: connection refused\"\n"
        "2026-06-29T10:00:03Z level=info msg=\"processing request\"\n"
    )
    result = _process_logs(logs)
    assert "connection refused" in result
    assert "request received" not in result
    assert "processing request" not in result


def test_fallback_to_all_non_noise_when_no_error_signals():
    logs = (
        "2026-06-29T10:00:01Z GET /healthz | 200 | 1ms\n"
        "2026-06-29T10:00:02Z level=info msg=\"startup complete\"\n"
        "2026-06-29T10:00:03Z level=info msg=\"listening on :8082\"\n"
    )
    result = _process_logs(logs)
    assert "startup complete" in result
    assert "listening on :8082" in result
    assert "/healthz" not in result


def test_dedup_collapses_repeated_lines():
    repeated = "\n".join(
        f"2026-06-29T10:00:0{i}Z level=error msg=\"dial tcp: connection refused\""
        for i in range(5)
    )
    result = _process_logs(repeated)
    lines = result.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("[×5]")
    assert "connection refused" in lines[0]


def test_dedup_preserves_unique_lines():
    logs = (
        "2026-06-29T10:00:01Z level=error msg=\"dial tcp: connection refused\"\n"
        "2026-06-29T10:00:02Z level=error msg=\"panic: runtime error: index out of range\"\n"
    )
    result = _process_logs(logs)
    lines = result.splitlines()
    assert len(lines) == 2
    assert not any(l.startswith("[×") for l in lines)
