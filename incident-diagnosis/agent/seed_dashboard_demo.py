"""Standalone local-only script — seeds demo incidents/timelines/pending-suggestions
into Redis for manual dashboard UI verification. Not wired into app.py or any Flask
route. Run manually: `REDIS_URL=redis://localhost:6379 python seed_dashboard_demo.py`.

Re-running merges new occurrences into any still-open incidents from a prior run
(same service+alert_name) rather than erroring — use POST /admin/reset-incidents
first for a clean slate.
"""
import os, time
from memory import (
    connect, record_incident_occurrence, append_incident_timeline,
    store_pending_suggestion, approve_pending_suggestion, resolve_incident,
)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")


def _step(name, duration_ms, **metadata):
    now = time.time()
    return {
        "type": "step", "name": name,
        "started_at": now - duration_ms / 1000, "finished_at": now,
        "duration_ms": duration_ms, "metadata": metadata,
    }


def _seed_occurrence(rdb, *, service, alert_name, namespace="vroom-dev",
                      pods_available=1, pods_desired=1, waiting_reason="CrashLoopBackOff",
                      last_terminated_reason="Error", restarts=6,
                      init_waiting_reason="", init_last_terminated_reason="", init_restarts=0,
                      log_error="", event_reason="", event_message="", event_object="",
                      template_diff=None, dependency=None,
                      root_cause="", dev_action="", kubectl_hint="", low_confidence=False,
                      quality_passed=True):
    occurrence = {
        "alert_name": alert_name, "service": service, "namespace": namespace,
        "pods_available": pods_available, "pods_desired": pods_desired,
        "waiting_reason": waiting_reason, "last_terminated_reason": last_terminated_reason,
        "restarts": restarts,
        "init_waiting_reason": init_waiting_reason,
        "init_last_terminated_reason": init_last_terminated_reason,
        "init_restarts": init_restarts,
        "log_error": log_error, "event_reason": event_reason,
        "event_message": event_message, "event_object": event_object,
        "template_diff": template_diff, "dependency": dependency,
        "root_cause": root_cause, "dev_action": dev_action, "kubectl_hint": kubectl_hint,
        "low_confidence": low_confidence,
    }
    iid = record_incident_occurrence(rdb, occurrence)

    append_incident_timeline(rdb, iid, _step(
        "collect_diagnostics", 420,
        pods_available=pods_available, pods_desired=pods_desired, waiting_reason=waiting_reason,
    ))
    append_incident_timeline(rdb, iid, _step("replicaset_diff", 160, found=template_diff is not None))
    append_incident_timeline(rdb, iid, _step("dependency_chase", 30, found=dependency is not None))
    append_incident_timeline(rdb, iid, _step("trusted_match_check", 8, trusted_match=False))
    append_incident_timeline(rdb, iid, _step("llm_phase1", 820, parsed=True))
    append_incident_timeline(rdb, iid, _step(
        "quality_check", 3, passed=quality_passed, low_confidence=low_confidence,
    ))
    if not quality_passed:
        append_incident_timeline(rdb, iid, _step("llm_refine", 640, parsed=True))
    append_incident_timeline(rdb, iid, _step("record_incident", 6, incident_id=iid))

    return iid


def run():
    rdb = connect(REDIS_URL)

    # Scenario 1: ride-service, 2 occurrences merged, all 5 evidence cards,
    # long-key approved KB suggestion. Occurrence 1 passes quality_check on the
    # first try; occurrence 2 fails it so llm_refine runs (exercises both paths).
    iid = _seed_occurrence(
        rdb, service="ride-service", alert_name="KubernetesPodNotHealthy",
        waiting_reason="CrashLoopBackOff", last_terminated_reason="Error", restarts=8,
        init_waiting_reason="", init_last_terminated_reason="Unknown", init_restarts=2,
        log_error="redis: 2026/07/07 13:59:08 pool.go:617: redis: connection pool: failed to "
                  "dial after 5 attempts: dial tcp: lookup bad-host on 10.43.0.10:53: no such host",
        event_reason="BackOff",
        event_message="Back-off restarting failed container ride-service in pod "
                       "ride-service-59db7888bc-2chmg_vroom-dev(dd01ba54-76e6-4051-a154-99543a69b4fa)",
        event_object="ride-service-59db7888bc-2chmg",
        template_diff={
            "env_changed": True,
            "env_diff": [{"key": "REDIS_ADDR", "old_value": "redis.platform.svc.cluster.local:6379",
                          "new_value": "bad-host:6379"}],
            "image_changed": False, "changed_at": "2026-06-30T14:00:47Z",
        },
        dependency={"namespace": "vroom-dev", "name": "redis", "pods_available": 1,
                    "pods_desired": 1, "waiting_reason": ""},
        root_cause="Insufficient evidence to confirm — observed: connection pool: failed to dial "
                   "after 5 attempts: dial tcp: lookup bad-host on 10.43.0.10:53: no such host",
        dev_action="Investigate recent env change from REDIS_ADDR: "
                   "redis.platform.svc.cluster.local:6379 to bad-host:6379",
        kubectl_hint="kubectl describe pod ride-service-59db7888bc-2chmg -n vroom-dev",
        low_confidence=True, quality_passed=True,
    )
    time.sleep(2)
    iid = _seed_occurrence(
        rdb, service="ride-service", alert_name="KubernetesPodNotHealthy",
        waiting_reason="CrashLoopBackOff", last_terminated_reason="Error", restarts=14,
        init_waiting_reason="", init_last_terminated_reason="Unknown", init_restarts=2,
        log_error="redis: 2026/07/07 14:24:10 pool.go:617: redis: connection pool: failed to "
                  "dial after 5 attempts: dial tcp: lookup bad-host on 10.43.0.10:53: no such host",
        event_reason="BackOff",
        event_message="Back-off restarting failed container ride-service in pod "
                       "ride-service-59db7888bc-2chmg_vroom-dev(dd01ba54-76e6-4051-a154-99543a69b4fa)",
        event_object="ride-service-59db7888bc-2chmg",
        template_diff={
            "env_changed": True,
            "env_diff": [{"key": "REDIS_ADDR", "old_value": "redis.platform.svc.cluster.local:6379",
                          "new_value": "bad-host:6379"}],
            "image_changed": False, "changed_at": "2026-06-30T14:00:47Z",
        },
        dependency={"namespace": "vroom-dev", "name": "redis", "pods_available": 1,
                    "pods_desired": 1, "waiting_reason": ""},
        root_cause="REDIS_ADDR env var points at bad-host:6379, an unresolvable hostname — "
                   "connection pool cannot establish a connection to Redis",
        dev_action="Revert REDIS_ADDR to redis.platform.svc.cluster.local:6379 via kubectl set env",
        kubectl_hint="kubectl set env deployment/ride-service "
                      "REDIS_ADDR=redis.platform.svc.cluster.local:6379 -n vroom-dev",
        low_confidence=False, quality_passed=False,
    )

    pid = store_pending_suggestion(rdb, {
        "service": "ride-service",
        "symptom": "ride-service repeatedly restarting; log shows dial tcp: lookup bad-host: no such host",
        "proposed_knowledge_key": "ride_service_redis_dependency_address_misconfigured_after_gitops_rollback",
        "is_new_knowledge_key": True,
        "root_cause": "A dependency address env var was changed to an invalid value, causing connection failures",
        "fix_action": "Check the ReplicaSet template diff and revert the env var with kubectl set env",
        "context_notes": "Confirmed manual kubectl set env hotfix during testing, not a real outage",
        "source_incident_id": iid,
    })
    approve_pending_suggestion(
        rdb, pid, actor="Alice", mode="new",
        knowledge_key="ride_service_redis_dependency_address_misconfigured_after_gitops_rollback",
        symptom="ride-service repeatedly restarting; log shows dial tcp: lookup bad-host: no such host",
        context_notes="Confirmed manual kubectl set env hotfix during testing, not a real outage",
        root_cause_pattern="A dependency address env var was changed to an invalid value, causing connection failures",
        fix_action="Check the ReplicaSet template diff and revert the env var with kubectl set env",
        conclusive=True,
    )

    # Scenario 2: dispatch-service, 1 occurrence, minimal evidence, no pending suggestion.
    _seed_occurrence(
        rdb, service="dispatch-service", alert_name="KubernetesPodCrashLooping",
        waiting_reason="CrashLoopBackOff", last_terminated_reason="Error", restarts=4,
        root_cause="dispatch-service container exited with a non-zero code repeatedly",
        dev_action="Check previous container logs for a startup crash or missing env var",
        kubectl_hint="kubectl logs deployment/dispatch-service --previous -n vroom-dev",
        quality_passed=True,
    )

    # Scenario 3: user-service, image change instead of env change.
    _seed_occurrence(
        rdb, service="user-service", alert_name="KubernetesPodNotHealthy",
        waiting_reason="ImagePullBackOff", last_terminated_reason="", restarts=0,
        event_reason="Failed",
        event_message='Failed to pull image "ghcr.io/ama2352/vroom-mvp-user:v1.4.0": not found',
        event_object="user-service-7d9f6c5b7-abcde",
        template_diff={
            "env_changed": False, "env_diff": [],
            "image_changed": True,
            "old_image": "ghcr.io/ama2352/vroom-mvp-user:v1.3.2",
            "new_image": "ghcr.io/ama2352/vroom-mvp-user:v1.4.0",
            "changed_at": "2026-07-07T09:12:00Z",
        },
        root_cause="Deployment references image tag v1.4.0 which does not exist in the registry",
        dev_action="Verify the v1.4.0 tag was pushed to GHCR; revert to v1.3.2 if not",
        kubectl_hint="kubectl set image deployment/user-service "
                      "user-service=ghcr.io/ama2352/vroom-mvp-user:v1.3.2 -n vroom-dev",
        quality_passed=True,
    )

    # Scenario 4: notification-service, resolved.
    nid = _seed_occurrence(
        rdb, service="notification-service", alert_name="KubernetesPodNotHealthy",
        waiting_reason="CrashLoopBackOff", last_terminated_reason="Error", restarts=3,
        root_cause="notification-service container exited with a non-zero code repeatedly",
        dev_action="Check previous container logs for a startup crash",
        kubectl_hint="kubectl logs deployment/notification-service --previous -n vroom-dev",
        quality_passed=True,
    )
    resolve_incident(rdb, nid, "Alice")

    print("Seeded 4 demo incidents (ride-service x2 occurrences, dispatch-service, "
          "user-service, notification-service[resolved]) + 1 approved pending suggestion.")


if __name__ == "__main__":
    run()
