from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from alerting import incident_window, normalize_alert


def test_normalize_alert_preserves_correlation_fields():
    alert = normalize_alert({
        "fingerprint": "fp-123", "starts_at": "2026-08-04T10:15:00Z",
        "alert_name": "DLQEventsDetected", "service": "dispatch-service",
        "namespace": "vroom-dev", "metric_value": "1", "threshold": "0",
    })
    assert alert["fingerprint"] == "fp-123"
    assert alert["service"] == "dispatch-service"
    assert alert["metric_value"] == 1.0
    assert "incident_kind" not in alert


def test_incident_window_is_anchored_to_alert_start():
    now = datetime(2026, 8, 4, 10, 20, tzinfo=timezone.utc)
    start, end = incident_window("2026-08-04T10:15:00Z", now)
    assert start == int(datetime(2026, 8, 4, 10, 10, tzinfo=timezone.utc).timestamp())
    assert end == int(now.timestamp())


def test_invalid_timestamp_is_visible_and_bounded():
    alert = normalize_alert({"starts_at": "not-a-time"})
    assert "starts_at_error" in alert
    assert alert["service"] == "unknown"
    assert alert["namespace"] == "unknown"
