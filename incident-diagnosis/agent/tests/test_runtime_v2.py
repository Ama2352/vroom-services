from evidence_projection import normalize_evidence
from runtime_v2 import build_raw_evidence, build_v2_occurrence


def test_occurrence_uses_v2_diagnosis_contract_and_raw_evidence_rows():
    template = normalize_evidence(
        {"alert_name": "DLQEventsDetected", "service": "dispatch-service", "metric_value": 3},
        {"pods_ready": 1, "pods_desired": 1, "restarts": 0},
        {"status": "found", "message": 'unknown event type "Trip.Requested.v2"'},
        {"status": "correlated", "error_service": "dispatch-service", "error_operation": "dispatch.consume.Trip.Requested.v2"},
        {"status": "unchanged", "changes": []},
    )
    evidence = build_raw_evidence(
        template, {"pods_ready": 1, "pods_desired": 1, "restarts": 0},
        {"status": "available", "request_rate": 0.2, "error_rate_percent": 0, "p95_latency_ms": 5},
        {"status": "found", "message": 'unknown event type "Trip.Requested.v2"'},
        {"status": "correlated", "service_path": ["ride-service", "dispatch-service"], "error_operation": "dispatch.consume.Trip.Requested.v2"},
        {"status": "unchanged", "changes": []},
    )
    occurrence = build_v2_occurrence(template, evidence, {
        "incident_summary": "dispatch-service rejected Trip.Requested.v2 and DLQ events increased.",
        "diagnosis_cause": None,
        "hypothesis": "ride-service and dispatch-service may support different event-contract versions.",
        "recommended_action": {"kind": "investigation", "summary": "Compare producer and consumer contract versions."},
        "evidence_analysis": {}, "used_knowledge_keys": [],
        "evidence_refs": ["log:selected"], "hypothesis_evidence_refs": ["log:selected", "trace:selected"],
    }, {"mode": "nearest", "examples": []})

    assert occurrence["diagnosis"]["diagnosis_cause"] is None
    assert occurrence["diagnosis"]["hypothesis"].startswith("ride-service")
    assert occurrence["raw_evidence"]["kubernetes"]["rows"] == [
        {"field": "ready", "value": "1 pod"},
        {"field": "desired", "value": "1 pod"},
        {"field": "restarts", "value": "0 pod restarts"},
    ]
    assert occurrence["raw_evidence"]["metrics"]["rows"][0]["unit"] == "req/s"
    assert "root_cause" not in occurrence
