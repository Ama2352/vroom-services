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
