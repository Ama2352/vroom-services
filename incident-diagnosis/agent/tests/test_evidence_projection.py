import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from evidence_projection import build_evidence_projection


def test_projection_keeps_available_sources_as_labelled_facts():
    projection = build_evidence_projection(
        {"alert_name": "DLQEventsDetected", "metric_value": 1, "threshold": 0},
        {"last_terminated_reason": "OOMKilled", "pods_ready": 0, "pods_desired": 1},
        {"status": "found", "message": "unknown event type Trip.Requested.v2"},
        {"status": "correlated", "trace_id": "abc"},
        None,
        {
            "status": "changed",
            "changes": [{"path": "resources.limits.memory", "previous": "300Mi", "current": "6Mi"}],
        },
    )

    lexical = projection.lexical_text()
    assert "alert.name: DLQEventsDetected" in lexical
    assert "runtime.last_terminated_reason: OOMKilled" in lexical
    assert "log.message: unknown event type Trip.Requested.v2" in lexical
    assert "config.resources.limits.memory.current: 6Mi" in lexical
    assert "trace.id: abc" in lexical
    assert "primary" not in lexical
    assert "incident_kind" not in lexical


def test_projection_tracks_stable_evidence_ids_without_role_classification():
    projection = build_evidence_projection(
        {"alert_name": "KubePodCrashLooping"},
        {"waiting_reason": "CrashLoopBackOff"},
        {"status": "unavailable"},
        {"status": "no_trace_id"},
        None,
        {"status": "unchanged", "changes": []},
    )

    assert projection.evidence_ids == frozenset({"alert:KubePodCrashLooping", "runtime:waiting_reason"})
    assert projection.to_prompt_dict()["runtime.waiting_reason"] == "CrashLoopBackOff"
