from unittest.mock import patch, MagicMock
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


def test_bundle_contains_service_name():
    side_effects = [_prom_ok(12.4), _prom_ok(8.3), _prom_ok(1.2), _loki_ok(47), MagicMock(ok=False)]
    with patch("requests.get", side_effect=side_effects):
        bundle = collector.collect_bundle("ride-service", "vroom-dev")
    assert "service=ride-service" in bundle
    assert "namespace=vroom-dev" in bundle


def test_bundle_includes_metrics():
    side_effects = [_prom_ok(12.4), _prom_ok(8.3), _prom_ok(1.2), _loki_ok(47), MagicMock(ok=False)]
    with patch("requests.get", side_effect=side_effects):
        bundle = collector.collect_bundle("ride-service", "vroom-dev")
    assert "rps=" in bundle
    assert "err=" in bundle
    assert "p99=" in bundle
    assert "loki_errors=" in bundle


def test_bundle_graceful_on_prometheus_failure():
    with patch("requests.get", side_effect=Exception("connection refused")):
        bundle = collector.collect_bundle("ride-service", "vroom-dev")
    # Should not raise; returns bundle with zeros
    assert "service=ride-service" in bundle
    assert "rps=0" in bundle
