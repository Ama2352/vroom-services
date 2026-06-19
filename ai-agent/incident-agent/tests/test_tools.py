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
    assert mock_get.call_args[1]["timeout"] == 5
