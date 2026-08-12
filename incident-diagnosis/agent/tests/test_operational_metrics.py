from unittest.mock import patch, MagicMock

import collector


def prom(value):
    response = MagicMock(ok=True)
    response.json.return_value = {"data": {"result": [{"value": [0, str(value)]}]}}
    return response


def test_operational_metrics_expose_units_and_p95():
    with patch("requests.get", side_effect=[prom(12), prom(0.2), prom(0.045), prom(250), prom(128), prom(50), prom(0.1)]):
        result = collector.collect_operational_metrics("ride-service", "vroom-dev")
    assert result["request_rate"]["unit"] == "req/s"
    assert result["http_error_rate"]["unit"] == "%"
    assert result["p95_latency"]["unit"] == "ms"
    assert result["memory_working_set"]["unit"] == "MiB"

