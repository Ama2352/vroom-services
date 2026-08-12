from evidence_projection import normalize_evidence


def test_template_has_fixed_order_and_removes_volatile_trace_id():
    template = normalize_evidence(
        alert={"alert_name": "DLQEventsDetected", "service": "dispatch-service"},
        facts={"waiting_reason": "", "event_reason": ""},
        log={
            "status": "found",
            "message": "unknown event type Trip.Requested.v2",
            "trace_id": "volatile-trace-id",
        },
        trace={
            "status": "correlated",
            "error_service": "dispatch-service",
            "error_operation": "dispatch.consume.Trip.Requested.v2",
        },
        configuration={"status": "unchanged", "changes": []},
    )

    assert [line.split(":", 1)[0] for line in template.serialize().splitlines()] == [
        "alert_name",
        "service",
        "triggering_metric",
        "waiting_reason",
        "last_terminated_reason",
        "event_reason",
        "event_message",
        "log_error",
        "trace_error_service",
        "trace_error_operation",
        "trace_error_message",
        "configuration_diff",
    ]
    assert "volatile-trace-id" not in template.serialize()


def test_template_fingerprint_ignores_trace_id_but_changes_for_configuration():
    common = {
        "alert": {"alert_name": "ServiceDown", "service": "ride-service"},
        "facts": {},
        "log": {"status": "found", "message": "dial tcp bad-host:6379"},
        "configuration": {
            "status": "changed",
            "changes": [{
                "path": "containers.ride-service.env.REDIS_ADDR",
                "previous": "redis.platform.svc.cluster.local:6379",
                "current": "bad-host:6379",
            }],
        },
    }
    first = normalize_evidence(**common, trace={"status": "correlated", "trace_id": "one"})
    second = normalize_evidence(**common, trace={"status": "correlated", "trace_id": "two"})
    changed = normalize_evidence(
        **{**common, "configuration": {
            "status": "changed",
            "changes": [{
                "path": "containers.ride-service.env.REDIS_ADDR",
                "previous": "redis.platform.svc.cluster.local:6379",
                "current": "other-host:6379",
            }],
        }},
        trace={"status": "correlated", "trace_id": "one"},
    )

    assert first.fingerprint() == second.fingerprint()
    assert first.fingerprint() != changed.fingerprint()
