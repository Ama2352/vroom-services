import fakeredis

from memory import get_incident_v2, list_incidents_v2, record_incident_v2


def test_records_latest_v2_occurrence_without_legacy_root_cause_fields():
    rdb = fakeredis.FakeRedis(decode_responses=True)
    incident_id = record_incident_v2(rdb, {
        "alert_name": "DLQEventsDetected", "service": "dispatch-service", "namespace": "vroom-dev",
        "diagnosis": {"incident_summary": "dispatch rejected an event.", "diagnosis_cause": None},
        "raw_evidence": {"logs": {"title": "Structured log"}},
        "retrieval": {"mode": "nearest"},
    })
    incident = get_incident_v2(rdb, incident_id)
    assert incident["diagnosis"]["incident_summary"] == "dispatch rejected an event."
    assert "root_cause" not in incident
    assert list_incidents_v2(rdb)[0]["id"] == incident_id
