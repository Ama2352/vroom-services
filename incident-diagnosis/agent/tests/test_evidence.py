from evidence import normalize_collected_evidence, normalize_evidence
from retrieval.evidence import EvidenceRetrievalService


class FakeCorpus:
    def __init__(self, documents):
        self.documents = tuple(documents)

    def get_documents(self):
        return self.documents


def test_retrieval_constructs_without_a_reranker_dependency():
    template = normalize_evidence(
        alert={"alert_name": "PodCrashLoop", "service": "orders"},
        facts={"waiting_reason": "CrashLoopBackOff"},
        log={"status": "found", "message": "connection refused"},
        trace={},
        configuration={"status": "unchanged", "changes": []},
    )
    corpus = FakeCorpus([])

    result = EvidenceRetrievalService(corpus).retrieve(template)

    assert result.mode.value == "none"
    assert result.candidates == ()


def test_template_has_fixed_order_and_excludes_volatile_trace_id():
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
        "alert_name", "service", "triggering_metric", "waiting_reason",
        "last_terminated_reason", "event_reason", "event_message", "log_error",
        "trace_error_service", "trace_error_operation", "trace_error_message",
        "configuration_diff",
    ]
    assert "volatile-trace-id" not in template.serialize()


def test_fingerprint_changes_when_configuration_evidence_changes():
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
    same_observations = normalize_evidence(**common, trace={"status": "correlated", "trace_id": "two"})
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

    assert first.fingerprint() == same_observations.fingerprint()
    assert first.fingerprint() != changed.fingerprint()


def test_collected_groups_map_to_the_stable_evidence_template():
    template = normalize_collected_evidence(
        {"alert_name": "PodCrashLoop", "service": "orders"},
        {
            "metrics": {"waiting_reason": "CrashLoopBackOff"},
            "logs": {"message": "connection refused"},
            "traces": {"error_service": "orders", "error_operation": "GET /checkout"},
            "kubernetes": {"event_reason": "BackOff", "event_message": "restart loop"},
            "configuration": {"status": "unchanged", "changes": []},
        },
    )

    assert "waiting_reason: CrashLoopBackOff" in template.serialize()
    assert "log_error: connection refused" in template.serialize()
    assert "trace_error_operation: GET /checkout" in template.serialize()
    assert {item["id"] for item in template.evidence} == {
        "alert:trigger", "log:selected", "trace:selected", "k8s:state",
    }
