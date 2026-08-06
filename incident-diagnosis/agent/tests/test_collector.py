from unittest.mock import patch, MagicMock
import requests
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import collector


def _prom_ok(value):
    resp = MagicMock()
    resp.ok = True
    resp.json.return_value = {"data": {"result": [{"value": [0, str(value)]}]}}
    return resp


def _prom_empty():
    resp = MagicMock()
    resp.ok = True
    resp.json.return_value = {"data": {"result": []}}
    return resp


def _loki_ok(n_results):
    resp = MagicMock()
    resp.ok = True
    resp.json.return_value = {"data": {"result": [{}] * n_results}}
    return resp


def _tempo_ok(n_traces, sample_name="POST /v1/trips"):
    resp = MagicMock()
    resp.ok = True
    resp.json.return_value = {"traces": [{"rootTraceName": sample_name}] * n_traces}
    return resp


def _tempo_empty():
    resp = MagicMock()
    resp.ok = True
    resp.json.return_value = {"traces": []}
    return resp


# call order: prom×3, loki, tempo
def _default_effects(prom_rps=12.4, prom_err=8.3, prom_p99=1.2, loki_n=47, tempo_n=0):
    tempo = _tempo_ok(tempo_n) if tempo_n > 0 else _tempo_empty()
    return [_prom_ok(prom_rps), _prom_ok(prom_err), _prom_ok(prom_p99),
            _loki_ok(loki_n), tempo]


def test_bundle_contains_service_name():
    with patch("requests.get", side_effect=_default_effects()):
        bundle = collector.collect_bundle("ride-service", "vroom-dev")
    assert "service=ride-service" in bundle
    assert "namespace=vroom-dev" in bundle
    assert "last_commit" not in bundle


def test_bundle_includes_metrics():
    with patch("requests.get", side_effect=_default_effects()):
        bundle = collector.collect_bundle("ride-service", "vroom-dev")
    assert "rps=" in bundle
    assert "err=" in bundle
    assert "p99=" in bundle
    assert "loki_errors=" in bundle
    assert "traces_errored=" in bundle


def test_bundle_traces_errored_when_tempo_has_results():
    with patch("requests.get", side_effect=_default_effects(tempo_n=3)):
        bundle = collector.collect_bundle("ride-service", "vroom-dev")
    assert "traces_errored=3" in bundle
    assert 'sample: "POST /v1/trips"' in bundle


def test_bundle_traces_errored_zero_when_none():
    with patch("requests.get", side_effect=_default_effects(tempo_n=0)):
        bundle = collector.collect_bundle("ride-service", "vroom-dev")
    assert "traces_errored=0" in bundle


def test_bundle_tempo_unavailable_returns_zero():
    # Tempo times out — should not raise, should include traces_errored=0
    side_effects = [_prom_ok(12.4), _prom_ok(8.3), _prom_ok(1.2),
                    _loki_ok(10), Exception("tempo timeout")]
    with patch("requests.get", side_effect=side_effects):
        bundle = collector.collect_bundle("ride-service", "vroom-dev")
    assert "traces_errored=0" in bundle
    assert "service=ride-service" in bundle


def test_bundle_graceful_on_prometheus_failure():
    with patch("requests.get", side_effect=Exception("connection refused")):
        bundle = collector.collect_bundle("ride-service", "vroom-dev")
    assert "service=ride-service" in bundle
    assert "rps=0" in bundle
    assert "traces_errored=0" in bundle


def test_collect_impact_uses_emitted_gin_metrics():
    with patch("requests.get", side_effect=[_prom_ok(20), _prom_ok(4), _prom_ok(1.25)]) as mock_get:
        impact = collector.collect_impact("ride-service", "vroom-dev")
    queries = [call.kwargs["params"]["query"] for call in mock_get.call_args_list]
    assert all('service="ride-service"' in query for query in queries)
    assert all('namespace="vroom-dev"' in query for query in queries)
    assert "gin_requests_total" in queries[0]
    assert impact == {
        "status": "available", "window": "5m",
        "request_rate": 20.0, "error_rate_percent": 20.0,
        "p99_seconds": 1.25, "errors": [],
    }


def test_collect_impact_does_not_turn_timeout_into_zero():
    with patch("requests.get", side_effect=Exception("prometheus timeout")):
        impact = collector.collect_impact("ride-service", "vroom-dev")
    assert impact["status"] == "unavailable"
    assert impact["request_rate"] is None
    assert impact["error_rate_percent"] is None
    assert impact["p99_seconds"] is None


def test_collect_impact_distinguishes_no_data():
    with patch("requests.get", return_value=_prom_empty()):
        impact = collector.collect_impact("ride-service", "vroom-dev")
    assert impact["status"] == "no_data"


def test_collect_impact_uses_alert_metric_for_dlq_when_http_metrics_are_sparse():
    with patch("requests.get", return_value=_prom_empty()):
        impact = collector.collect_impact(
            "dispatch-service", "vroom-dev",
            alert={"alert_name": "DLQEventsDetected", "metric_value": 1.11, "threshold": 0},
        )
    assert impact["status"] == "available"
    assert impact["triggering_metric"] == {
        "name": "DLQ events", "value": 1.11, "threshold": 0.0,
    }
