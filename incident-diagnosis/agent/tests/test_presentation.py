import json

from presentation import build_presentation, compact_investigation_response


HOTFIX_CHAIN = {
    "incident_kind": "generic",
    "primary": [
        {"id": "log:selected", "role": "primary", "status": "available",
         "payload": {"message": "lookup bad-host: no such host"}},
    ],
    "causal_context": [
        {"id": "change:template", "role": "causal_context", "status": "available",
         "payload": {"classification": "hotfix", "matched_identifiers": ["bad-host"]}},
    ],
    "trigger": [{"id": "metric:service_impact", "role": "trigger", "status": "available",
                 "payload": {"name": "service_impact", "value": 0}}],
    "secondary": [],
    "consequence": [],
    "required": [],
}

HOTFIX_FACTS = {
    "template_diff": {"env_diff": [{"key": "REDIS_ADDR", "old_value": "redis.platform:6379", "new_value": "bad-host:6379"}],
                       "changed_at": "2026-08-10T04:02:27Z"},
    "pods_ready": 0, "pods_desired": 1,
}
HOTFIX_IMPACT = {"status": "available", "request_rate": 0.2, "error_rate_percent": 100}
HOTFIX_LOG = {"status": "found", "message": "lookup bad-host: no such host"}
HOTFIX_TRACE = {"status": "correlated", "trace_id": "trace-hotfix", "error_message": "lookup bad-host: no such host"}

DLQ_CHAIN = {
    "incident_kind": "dlq",
    "primary": [{"id": "log:selected", "role": "primary", "status": "available",
                 "payload": {"message": 'unknown event type "Trip.Requested.v2"'}}],
    "causal_context": [{"id": "change:provenance", "role": "causal_context", "status": "available",
                        "payload": {"causal_status": {"status": "recent_context"}}}],
    "trigger": [{"id": "metric:dlq_events", "role": "trigger", "status": "available",
                 "payload": {"name": "dlq_events", "value": 1}}],
    "secondary": [], "consequence": [], "required": [],
}
DLQ_FACTS = {"pods_ready": 1, "pods_desired": 1, "template_diff": {"image_changed": True}}
DLQ_IMPACT = {"status": "available", "request_rate": 0.2, "error_rate_percent": None}
DLQ_LOG = {"status": "found", "message": 'unknown event type "Trip.Requested.v2"'}
DLQ_TRACE = {"status": "correlated", "trace_id": "trace-dlq", "error_message": 'unknown event type "Trip.Requested.v2"'}


def test_confirmed_hotfix_exposes_causal_basis_and_remediation():
    view = build_presentation(
        alert={"alert_name": "ServiceDown", "starts_at": "2026-08-10T04:03:15Z"},
        diagnosis={
            "root_cause": "ride-service cannot resolve Redis at bad-host",
            "dev_action": "Restore the previous Redis address.",
            "kubectl_hint": "kubectl set env deployment/ride-service -n vroom-dev REDIS_ADDR=redis.platform:6379",
            "diagnosis_decision": {"status": "accepted", "published_generated_answer": True},
        },
        diagnosis_confidence={"level": "high", "reasons": [], "missing_evidence": []},
        evidence_chain=HOTFIX_CHAIN, facts=HOTFIX_FACTS, impact=HOTFIX_IMPACT,
        log_evidence=HOTFIX_LOG, trace_handoff=HOTFIX_TRACE,
        retrieval_support={"mode": "none", "accepted": False},
    )
    assert view["verdict"] == "cause_confirmed"
    assert view["causal_basis"]
    assert view["evidence_gap"] is None
    assert view["recommended_response"]["mode"] == "remediation"


def test_confirmed_dlq_failure_without_related_change_withholds_cause():
    diagnosis = {
        "root_cause": 'Insufficient evidence to confirm — observed: unknown event type "Trip.Requested.v2".',
        "dev_action": "delete the dispatch pod",
        "kubectl_hint": "kubectl logs <dispatch-service-pod-name>",
        "low_confidence": True,
        "diagnosis_decision": {"status": "rejected_after_refine", "published_generated_answer": False},
    }
    view = build_presentation(
        alert={"alert_name": "DLQEventsDetected", "service": "dispatch", "starts_at": "2026-08-10T08:02:39Z"},
        diagnosis=diagnosis,
        diagnosis_confidence={"level": "high", "reasons": [], "missing_evidence": []},
        evidence_chain=DLQ_CHAIN, facts=DLQ_FACTS, impact=DLQ_IMPACT,
        log_evidence=DLQ_LOG, trace_handoff=DLQ_TRACE,
        retrieval_support={"mode": "none", "accepted": False},
    )
    assert view["verdict"] == "review_required"
    assert view["confirmed_failure"] == 'dispatch rejected event type "Trip.Requested.v2"'
    assert view["causal_basis"] is None
    assert view["evidence_gap"]
    assert view["recommended_response"]["mode"] == "investigation"
    assert "<dispatch-service-pod-name>" not in json.dumps(view)


def test_presentation_labels_metric_units_and_does_not_duplicate_trace_as_log():
    view = build_presentation(
        alert={"alert_name": "DLQEventsDetected", "service": "dispatch-service"},
        diagnosis={"diagnosis_decision": {"status": "rejected_after_refine"}},
        diagnosis_confidence={"level": "high", "reasons": [], "missing_evidence": []},
        evidence_chain={**DLQ_CHAIN, "primary": [
            {"id": "log:selected", "payload": {"message": "unknown event type Trip.Requested.v2", "log_format": "structured"}},
            {"id": "trace:trace-dlq", "payload": {"trace_id": "trace-dlq", "status": "correlated"}},
        ]}, facts=DLQ_FACTS, impact=DLQ_IMPACT, log_evidence=DLQ_LOG,
        trace_handoff={**DLQ_TRACE, "grafana_url": "http://grafana/explore?left=%7B%22query%22%3A%20%22trace-dlq%22%7D"},
        retrieval_support={"mode": "none", "accepted": False},
    )
    kinds = [item["kind"] for item in view["supporting_evidence"]]
    assert kinds.count("log") == 1
    assert kinds.count("trace") == 1
    metric = next(item for item in view["supporting_evidence"] if item["kind"] == "metric")
    assert metric["value"].endswith("events")


def test_dlq_presentation_distinguishes_confirmed_mechanism_from_unproven_attribution():
    diagnosis = {
        "root_cause": 'dispatch rejected event type "Trip.Requested.v2"; event-contract compatibility is unproven',
        "dev_action": "Do not run a remediation command until the diagnosis is reviewed.",
        "diagnosis_decision": {
            "status": "accepted_with_unproven_attribution",
            "published_generated_answer": False,
            "published_operator_diagnosis": True,
        },
        "failure_status": "confirmed", "mechanism_status": "confirmed", "attribution_status": "unproven",
    }
    view = build_presentation(
        alert={"alert_name": "DLQEventsDetected", "service": "dispatch-service"}, diagnosis=diagnosis,
        diagnosis_confidence={"level": "high", "reasons": [], "missing_evidence": []},
        evidence_chain=DLQ_CHAIN, facts=DLQ_FACTS, impact=DLQ_IMPACT,
        log_evidence=DLQ_LOG, trace_handoff=DLQ_TRACE,
        retrieval_support={"mode": "none", "accepted": False},
    )
    assert view["confirmed_failure"] == 'dispatch-service rejected event type "Trip.Requested.v2"'
    assert view["mechanism_status"] == "confirmed"
    assert view["attribution_status"] == "unproven"
    assert "cause not established" not in view["headline"].lower()
    assert "attribution" in view["evidence_gap"].lower()


def test_presentation_uses_operational_copy_and_bounds_evidence():
    view = build_presentation(
        alert={"alert_name": "ServiceDown"}, diagnosis={"root_cause": "unknown"},
        diagnosis_confidence={"level": "unknown", "reasons": [], "missing_evidence": ["trace unavailable"]},
        evidence_chain={"incident_kind": "generic", "primary": [], "causal_context": [], "trigger": [], "secondary": [], "consequence": []},
        facts={}, impact={}, log_evidence={"status": "unavailable"}, trace_handoff={"status": "unavailable"},
        retrieval_support={},
    )
    assert "what we know" not in json.dumps(view).lower()
    assert "why we" not in json.dumps(view).lower()
    assert len(view["supporting_evidence"]) <= 4


def test_compact_investigation_response_excludes_raw_debug_fields():
    presentation = {
        "verdict": "review_required", "headline": "failure observed", "evidence_confidence": "high",
        "answer_source": "safe_fallback", "recommended_response": {"mode": "investigation", "summary": "check logs"},
    }
    body = compact_investigation_response(
        incident_id="incident-1", service="dispatch-service", namespace="vroom-dev",
        alert_name="DLQEventsDetected", presentation=presentation,
        trace_handoff={"status": "correlated", "trace_id": "trace-1", "grafana_url": "http://grafana/trace-1"},
        retrieval_support={"mode": "none", "accepted": False, "debug": {"bundle": "must not leak"}},
    )
    assert body["incident_id"] == "incident-1"
    assert body["trace"]["trace_id"] == "trace-1"
    assert "bundle" not in json.dumps(body)
