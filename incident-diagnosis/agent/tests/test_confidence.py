import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from confidence import assess_confidence

ALERT = {"alert_name": "DLQEventsDetected"}
FACTS = {"kubernetes": [], "changes": [], "dependencies": []}
IMPACT = {"status": "available", "error_rate_percent": 18.7, "p99_seconds": 2.8, "request_rate": 24.3}
LOG = {"status": "found", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "message": "error"}


@pytest.mark.parametrize("impact,log,trace,expected", [
    (IMPACT, LOG, {"status": "correlated"}, "high"),
    (IMPACT, LOG, {"status": "no_trace_id"}, "medium"),
    (IMPACT, {"status": "no_match"}, {"status": "no_trace_id"}, "low"),
    ({"status": "unavailable"}, {"status": "unavailable"}, {"status": "unavailable"}, "unknown"),
    (IMPACT, LOG, {"status": "conflict"}, "unknown"),
])
def test_confidence_table(impact, log, trace, expected):
    result = assess_confidence(ALERT, impact, log, trace, FACTS)
    assert result["level"] == expected
    assert result["reasons"] or result["missing_evidence"]
