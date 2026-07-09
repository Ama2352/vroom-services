import json, time
import pytest
from unittest.mock import patch

# fakeredis lets us test Redis operations without a real server
try:
    import fakeredis
except ImportError:
    pytest.skip("fakeredis not installed — run: pip install fakeredis", allow_module_level=True)

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import memory


@pytest.fixture
def rdb():
    return fakeredis.FakeRedis()


def test_tokenize_splits_on_punctuation():
    assert memory._tokenize("postgres-primary:5432") == ["postgres", "primary", "5432"]


def test_tokenize_lowercases():
    assert memory._tokenize("CrashLoopBackOff") == ["crashloopbackoff"]


def test_tokenize_empty_string_returns_empty_list():
    assert memory._tokenize("") == []


def test_tokenize_splits_on_whitespace_and_slashes():
    assert memory._tokenize("dial tcp i/o timeout") == ["dial", "tcp", "i", "o", "timeout"]


def _make_occurrence(**kwargs):
    base = {
        "alert_name": "HighErrorRate",
        "service": "ride-service",
        "namespace": "vroom-dev",
        "pods_available": 0, "pods_desired": 1,
        "waiting_reason": "", "last_terminated_reason": "", "restarts": 0,
        "init_waiting_reason": "", "init_last_terminated_reason": "", "init_restarts": 0,
        "log_error": "", "event_reason": "", "event_message": "", "event_object": "",
        "root_cause": "dispatch consumer stale cursor",
        "dev_action": "restart the dispatch consumer",
        "kubectl_hint": "kubectl rollout restart deployment/dispatch-service -n vroom-dev",
        "low_confidence": False,
    }
    base.update(kwargs)
    return base


def test_build_symptom_text_includes_all_fields():
    text = memory.build_symptom_text("HighErrorRate", "CrashLoopBackOff", "dial tcp timeout")
    assert text == "HighErrorRate CrashLoopBackOff dial tcp timeout"


def test_build_symptom_text_handles_missing_optional_fields():
    assert memory.build_symptom_text("Alert") == "Alert"


def test_build_symptom_text_symmetry_between_storage_and_query_call_sites():
    # The exact scenario the original symmetry bug fix guards against: text built for
    # storage and text built for a later query, from the same underlying facts, must
    # be identical.
    stored_text = memory.build_symptom_text("PodCrash", "OOMKilled", "exit code 137")
    query_text  = memory.build_symptom_text("PodCrash", "OOMKilled", "exit code 137")
    assert stored_text == query_text


def _make_knowledge(**kwargs):
    base = {
        "key": "oom",
        "root_cause_pattern": "Container exceeded its memory limit and was OOMKilled",
        "fix_action": "Increase the memory limit in the deployment manifest.",
        "trigger_waiting_reason": "OOMKilled",
        "conclusive": True,
        "source": "bootstrap",
        "created_by": "bootstrap",
    }
    base.update(kwargs)
    return base


def test_store_knowledge_entry_creates_hash_and_index(rdb):
    key = memory.store_knowledge_entry(rdb, _make_knowledge())
    assert key == "oom"
    assert rdb.hexists("knowledge:entry:oom", "root_cause_pattern")
    assert rdb.sismember(memory.KNOWLEDGE_INDEX, "oom")


def test_get_knowledge_entry_returns_dict_with_bool_conclusive(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge(conclusive=True))
    entry = memory.get_knowledge_entry(rdb, "oom")
    assert entry["conclusive"] is True
    assert entry["root_cause_pattern"] == "Container exceeded its memory limit and was OOMKilled"


def test_get_knowledge_entry_missing_returns_none(rdb):
    assert memory.get_knowledge_entry(rdb, "does_not_exist") is None


def test_get_knowledge_entry_false_conclusive_roundtrips(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge(key="crashloop", conclusive=False))
    entry = memory.get_knowledge_entry(rdb, "crashloop")
    assert entry["conclusive"] is False


def test_list_knowledge_entries_returns_all(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge(key="oom"))
    memory.store_knowledge_entry(rdb, _make_knowledge(key="crashloop", conclusive=False))
    entries = memory.list_knowledge_entries(rdb)
    assert {e["key"] for e in entries} == {"oom", "crashloop"}


def test_list_knowledge_entries_empty(rdb):
    assert memory.list_knowledge_entries(rdb) == []


def test_update_knowledge_entry_changes_fields(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge())
    ok = memory.update_knowledge_entry(rdb, "oom", {
        "root_cause_pattern": "Updated pattern",
        "last_modified_by": "Alice",
    })
    assert ok is True
    entry = memory.get_knowledge_entry(rdb, "oom")
    assert entry["root_cause_pattern"] == "Updated pattern"
    assert entry["last_modified_by"] == "Alice"
    assert entry["last_modified_at"] != ""


def test_update_knowledge_entry_missing_returns_false(rdb):
    assert memory.update_knowledge_entry(rdb, "does_not_exist", {"fix_action": "x"}) is False


def test_update_knowledge_entry_sets_trigger_waiting_reason(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge(trigger_waiting_reason=""))
    ok = memory.update_knowledge_entry(rdb, "oom", {"trigger_waiting_reason": "OOMKilled"})
    assert ok is True
    entry = memory.get_knowledge_entry(rdb, "oom")
    assert entry["trigger_waiting_reason"] == "OOMKilled"


def test_delete_knowledge_entry_removes_hash_and_index(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge())
    result = memory.delete_knowledge_entry(rdb, "oom")
    assert result == "deleted"
    assert memory.get_knowledge_entry(rdb, "oom") is None
    assert not rdb.sismember(memory.KNOWLEDGE_INDEX, "oom")


def test_delete_knowledge_entry_missing_returns_not_found(rdb):
    assert memory.delete_knowledge_entry(rdb, "does_not_exist") == "not_found"


def test_delete_knowledge_entry_refused_when_history_exists(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge())
    memory.store_history_entry(rdb, {
        "service": "ride", "knowledge_key": "oom", "symptom": "ride OOMKilled",
        "context_notes": "", "source": "learned", "created_by": "Alice",
    })
    result = memory.delete_knowledge_entry(rdb, "oom")
    assert result == "has_history"
    assert memory.get_knowledge_entry(rdb, "oom") is not None


def _make_history(**kwargs):
    base = {
        "service": "ride-service",
        "knowledge_key": "oom",
        "symptom": "ride-service OOMKilled during load spike",
        "context_notes": "happened after batch job added, limit was 256Mi",
        "source": "learned",
        "created_by": "Alice",
    }
    base.update(kwargs)
    return base


def test_store_history_entry_creates_hash_and_index(rdb):
    hid = memory.store_history_entry(rdb, _make_history())
    assert rdb.hexists(f"history:entry:{hid}", "symptom")
    assert rdb.sismember(memory.HISTORY_INDEX, hid)


def test_get_history_entry_includes_id(rdb):
    hid = memory.store_history_entry(rdb, _make_history())
    entry = memory.get_history_entry(rdb, hid)
    assert entry["id"] == hid
    assert entry["knowledge_key"] == "oom"


def test_get_history_entry_missing_returns_none(rdb):
    assert memory.get_history_entry(rdb, "does-not-exist") is None


def test_list_history_entries_for_knowledge_filters_by_key(rdb):
    memory.store_history_entry(rdb, _make_history(knowledge_key="oom"))
    memory.store_history_entry(rdb, _make_history(knowledge_key="crashloop"))
    results = memory.list_history_entries_for_knowledge(rdb, "oom")
    assert len(results) == 1
    assert results[0]["knowledge_key"] == "oom"


def test_list_history_entries_for_knowledge_empty(rdb):
    assert memory.list_history_entries_for_knowledge(rdb, "oom") == []


def test_list_all_history_entries_returns_everything(rdb):
    memory.store_history_entry(rdb, _make_history(knowledge_key="oom"))
    memory.store_history_entry(rdb, _make_history(knowledge_key="crashloop"))
    assert len(memory.list_all_history_entries(rdb)) == 2


def test_update_history_entry_changes_fields(rdb):
    hid = memory.store_history_entry(rdb, _make_history())
    ok = memory.update_history_entry(rdb, hid, {
        "symptom": "updated symptom", "last_modified_by": "Bob",
    })
    assert ok is True
    entry = memory.get_history_entry(rdb, hid)
    assert entry["symptom"] == "updated symptom"
    assert entry["last_modified_by"] == "Bob"
    assert entry["last_modified_at"] != ""


def test_update_history_entry_missing_returns_false(rdb):
    assert memory.update_history_entry(rdb, "does-not-exist", {"symptom": "x"}) is False


def test_delete_history_entry_removes_hash_and_index(rdb):
    hid = memory.store_history_entry(rdb, _make_history())
    assert memory.delete_history_entry(rdb, hid) is True
    assert memory.get_history_entry(rdb, hid) is None
    assert not rdb.sismember(memory.HISTORY_INDEX, hid)


def test_delete_history_entry_missing_returns_false(rdb):
    assert memory.delete_history_entry(rdb, "does-not-exist") is False


# ── D4 trusted-match algorithm ────────────────────────────────────────────────

def test_derive_reason_signal_prefers_init_terminated_over_everything():
    facts = {"waiting_reason": "CrashLoopBackOff", "init_last_terminated_reason": "OOMKilled"}
    assert memory._derive_reason_signal(facts) == "Init:OOMKilled"


def test_derive_reason_signal_treats_unknown_last_terminated_as_no_signal():
    facts = {"waiting_reason": "CrashLoopBackOff", "last_terminated_reason": "Unknown"}
    assert memory._derive_reason_signal(facts) == "CrashLoopBackOff"


def test_derive_reason_signal_treats_unknown_init_terminated_as_no_signal():
    facts = {"init_waiting_reason": "CrashLoopBackOff", "init_last_terminated_reason": "Unknown"}
    assert memory._derive_reason_signal(facts) == "Init:CrashLoopBackOff"


def test_derive_reason_signal_init_waiting():
    facts = {"waiting_reason": "PodInitializing", "init_waiting_reason": "CrashLoopBackOff"}
    assert memory._derive_reason_signal(facts) == "Init:CrashLoopBackOff"


def test_derive_reason_signal_last_terminated_over_waiting():
    facts = {"waiting_reason": "CrashLoopBackOff", "last_terminated_reason": "OOMKilled"}
    assert memory._derive_reason_signal(facts) == "OOMKilled"


def test_derive_reason_signal_plain_waiting_reason():
    assert memory._derive_reason_signal({"waiting_reason": "CrashLoopBackOff"}) == "CrashLoopBackOff"


def test_derive_reason_signal_canonicalizes_err_image_pull():
    assert memory._derive_reason_signal({"waiting_reason": "ErrImagePull"}) == "ImagePullBackOff"


def test_derive_reason_signal_event_reason_fallback():
    facts = {"waiting_reason": "", "event_reason": "FailedScheduling"}
    assert memory._derive_reason_signal(facts) == "FailedScheduling"


def test_derive_reason_signal_zero_replicas():
    facts = {"waiting_reason": "", "pods_available": 0, "pods_desired": 3}
    assert memory._derive_reason_signal(facts) == "ZeroReplicas"


def test_derive_reason_signal_dependency_unhealthy():
    facts = {
        "waiting_reason": "",
        "pods_available": 1, "pods_desired": 1,
        "dependency": {"name": "postgres", "namespace": "platform", "pods_available": 0, "pods_desired": 0, "waiting_reason": ""}
    }
    assert memory._derive_reason_signal(facts) == "Dependency:postgres:ZeroReplicas"

    facts2 = {
        "waiting_reason": "",
        "pods_available": 1, "pods_desired": 1,
        "dependency": {"name": "postgres", "namespace": "platform", "pods_available": 1, "pods_desired": 2, "waiting_reason": ""}
    }
    assert memory._derive_reason_signal(facts2) == "Dependency:postgres:Unhealthy"

    facts3 = {
        "waiting_reason": "",
        "pods_available": 1, "pods_desired": 1,
        "dependency": {"name": "postgres", "namespace": "platform", "pods_available": 0, "pods_desired": 1, "waiting_reason": "CrashLoopBackOff"}
    }
    assert memory._derive_reason_signal(facts3) == "Dependency:postgres:CrashLoopBackOff"


def test_derive_reason_signal_dependency_priority_over_event_reason():
    facts = {
        "waiting_reason": "",
        "event_reason": "Unhealthy",
        "pods_available": 1, "pods_desired": 1,
        "dependency": {"name": "postgres", "namespace": "platform", "pods_available": 0, "pods_desired": 0, "waiting_reason": ""}
    }
    assert memory._derive_reason_signal(facts) == "Dependency:postgres:ZeroReplicas"


def test_derive_reason_signal_empty_when_nothing_matches():
    facts = {"waiting_reason": "", "pods_available": 1, "pods_desired": 1}
    assert memory._derive_reason_signal(facts) == ""


def test_token_coverage_full_overlap():
    assert memory._token_coverage("oom killed pod", "oom killed pod details") == 1.0


def test_token_coverage_partial_overlap():
    score = memory._token_coverage("oom killed pod", "oom something else")
    assert 0.0 < score < 1.0


def test_token_coverage_zero_overlap():
    assert memory._token_coverage("oom killed pod", "completely unrelated text") == 0.0


def test_token_coverage_empty_query_returns_zero():
    assert memory._token_coverage("", "anything") == 0.0


def test_find_trusted_match_conclusive_short_circuit(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge(
        key="oom", trigger_waiting_reason="OOMKilled", conclusive=True))
    facts = {"waiting_reason": "", "last_terminated_reason": "OOMKilled",
             "pods_available": 0, "pods_desired": 1}
    match = memory.find_trusted_match(rdb, facts, "SomeAlert OOMKilled")
    assert match["source"] == "knowledge"
    assert match["knowledge_key"] == "oom"
    assert match["context_notes"] == ""


def test_find_trusted_match_non_conclusive_does_not_short_circuit_without_pool_survivor(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge(
        key="crashloop", trigger_waiting_reason="CrashLoopBackOff", conclusive=False,
        root_cause_pattern="generic crashloop text unrelated to query"))
    facts = {"waiting_reason": "CrashLoopBackOff", "pods_available": 0, "pods_desired": 1}
    match = memory.find_trusted_match(rdb, facts, "totally different words here")
    assert match is None


def test_find_trusted_match_non_conclusive_knowledge_wins_pool_when_scored_high(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge(
        key="crashloop", trigger_waiting_reason="CrashLoopBackOff", conclusive=False,
        root_cause_pattern="postgres unreachable connection refused"))
    facts = {"waiting_reason": "CrashLoopBackOff", "pods_available": 0, "pods_desired": 1}
    match = memory.find_trusted_match(rdb, facts, "CrashLoopBackOff postgres unreachable connection refused")
    assert match["source"] == "knowledge"
    assert match["knowledge_key"] == "crashloop"


def test_find_trusted_match_history_entry_resolves_parent_knowledge(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge(
        key="crashloop", trigger_waiting_reason="CrashLoopBackOff", conclusive=False,
        root_cause_pattern="generic crashloop", fix_action="generic fix"))
    memory.store_history_entry(rdb, _make_history(
        knowledge_key="crashloop", symptom="ride-service DB connection refused postgres unreachable"))
    facts = {"waiting_reason": "CrashLoopBackOff", "pods_available": 0, "pods_desired": 1}
    match = memory.find_trusted_match(rdb, facts, "DB connection refused postgres unreachable")
    assert match["source"] == "history"
    assert match["knowledge_key"] == "crashloop"
    assert match["root_cause_pattern"] == "generic crashloop"
    assert match["context_notes"] == "happened after batch job added, limit was 256Mi"


def test_find_trusted_match_history_competes_even_without_signal(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge(key="outbox_stuck", trigger_waiting_reason="",
                                                        conclusive=False, root_cause_pattern="outbox worker stalled"))
    memory.store_history_entry(rdb, _make_history(
        knowledge_key="outbox_stuck", symptom="ride-service outbox events not draining redis stream"))
    facts = {"waiting_reason": "", "pods_available": 1, "pods_desired": 1}
    match = memory.find_trusted_match(rdb, facts, "outbox events not draining redis stream")
    assert match is not None
    assert match["knowledge_key"] == "outbox_stuck"


def test_find_trusted_match_below_threshold_returns_none(rdb):
    memory.store_history_entry(rdb, _make_history(knowledge_key="oom", symptom="one two three four five"))
    memory.store_knowledge_entry(rdb, _make_knowledge(key="oom"))
    facts = {"waiting_reason": "", "pods_available": 1, "pods_desired": 1}
    match = memory.find_trusted_match(rdb, facts, "one six seven eight nine ten")
    assert match is None


def test_find_trusted_match_empty_store_returns_none(rdb):
    facts = {"waiting_reason": "OOMKilled", "pods_available": 0, "pods_desired": 1}
    assert memory.find_trusted_match(rdb, facts, "OOMKilled") is None


def _make_pending(**kwargs):
    base = {
        "service": "ride-service",
        "symptom": "ride-service pods CrashLoopBackOff, DB unreachable",
        "proposed_knowledge_key": "crashloop",
        "is_new_knowledge_key": False,
        "root_cause": "",
        "fix_action": "",
        "context_notes": "seen during load test on 2026-07-06",
        "source_incident_id": "incident-123",
    }
    base.update(kwargs)
    return base


def test_store_pending_suggestion_creates_hash_with_pending_status(rdb):
    pid = memory.store_pending_suggestion(rdb, _make_pending())
    entry = memory.get_pending_suggestion(rdb, pid)
    assert entry["status"] == "pending"
    assert entry["service"] == "ride-service"
    assert rdb.sismember(memory.PENDING_INDEX, pid)


def test_get_pending_suggestion_missing_returns_none(rdb):
    assert memory.get_pending_suggestion(rdb, "does-not-exist") is None


def test_get_pending_suggestion_bool_field_roundtrips(rdb):
    pid = memory.store_pending_suggestion(rdb, _make_pending(is_new_knowledge_key=True))
    entry = memory.get_pending_suggestion(rdb, pid)
    assert entry["is_new_knowledge_key"] is True


def test_list_pending_suggestions_defaults_to_all(rdb):
    memory.store_pending_suggestion(rdb, _make_pending())
    memory.store_pending_suggestion(rdb, _make_pending(service="dispatch-service"))
    assert len(memory.list_pending_suggestions(rdb)) == 2


def test_list_pending_suggestions_filters_by_status(rdb):
    pid1 = memory.store_pending_suggestion(rdb, _make_pending())
    memory.store_pending_suggestion(rdb, _make_pending())
    memory.reject_pending_suggestion(rdb, pid1, "Alice")
    pending_only = memory.list_pending_suggestions(rdb, status="pending")
    rejected_only = memory.list_pending_suggestions(rdb, status="rejected")
    assert len(pending_only) == 1
    assert len(rejected_only) == 1
    assert rejected_only[0]["id"] == pid1


def test_approve_pending_suggestion_existing_key_creates_only_history(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge(key="crashloop"))
    pid = memory.store_pending_suggestion(rdb, _make_pending())
    hid = memory.approve_pending_suggestion(
        rdb, pid, actor="Alice", mode="existing", knowledge_key="crashloop",
        symptom="edited symptom text", context_notes="edited notes")
    assert hid is not None
    history = memory.get_history_entry(rdb, hid)
    assert history["knowledge_key"] == "crashloop"
    assert history["symptom"] == "edited symptom text"
    assert history["created_by"] == "Alice"
    assert len(memory.list_knowledge_entries(rdb)) == 1


def test_approve_pending_suggestion_new_key_creates_knowledge_and_history(rdb):
    pid = memory.store_pending_suggestion(rdb, _make_pending(
        proposed_knowledge_key="outbox_stuck", is_new_knowledge_key=True))
    hid = memory.approve_pending_suggestion(
        rdb, pid, actor="Bob", mode="new", knowledge_key="outbox_stuck",
        symptom="edited symptom", context_notes="edited notes",
        root_cause_pattern="edited root cause", fix_action="edited fix", conclusive=False)
    knowledge = memory.get_knowledge_entry(rdb, "outbox_stuck")
    assert knowledge is not None
    assert knowledge["root_cause_pattern"] == "edited root cause"
    assert knowledge["created_by"] == "Bob"
    history = memory.get_history_entry(rdb, hid)
    assert history["knowledge_key"] == "outbox_stuck"


def test_approve_pending_suggestion_new_key_sets_trigger_waiting_reason(rdb):
    pid = memory.store_pending_suggestion(rdb, _make_pending(
        proposed_knowledge_key="bad_dependency_address", is_new_knowledge_key=True))
    memory.approve_pending_suggestion(
        rdb, pid, actor="Alice", mode="new", knowledge_key="bad_dependency_address",
        symptom="s", context_notes="", root_cause_pattern="rc", fix_action="fix",
        conclusive=True, trigger_waiting_reason="CrashLoopBackOff")
    knowledge = memory.get_knowledge_entry(rdb, "bad_dependency_address")
    assert knowledge["trigger_waiting_reason"] == "CrashLoopBackOff"


def test_approve_pending_suggestion_defaults_trigger_waiting_reason_to_empty(rdb):
    pid = memory.store_pending_suggestion(rdb, _make_pending(
        proposed_knowledge_key="outbox_stuck2", is_new_knowledge_key=True))
    memory.approve_pending_suggestion(
        rdb, pid, actor="Bob", mode="new", knowledge_key="outbox_stuck2",
        symptom="s", context_notes="", root_cause_pattern="rc", fix_action="fix",
        conclusive=False)
    knowledge = memory.get_knowledge_entry(rdb, "outbox_stuck2")
    assert knowledge["trigger_waiting_reason"] == ""


def test_approve_pending_suggestion_sets_status_and_actor(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge(key="crashloop"))
    pid = memory.store_pending_suggestion(rdb, _make_pending())
    memory.approve_pending_suggestion(
        rdb, pid, actor="Alice", mode="existing", knowledge_key="crashloop",
        symptom="x", context_notes="")
    entry = memory.get_pending_suggestion(rdb, pid)
    assert entry["status"] == "approved"
    assert entry["decided_by"] == "Alice"
    assert entry["decided_at"] != ""


def test_approve_pending_suggestion_does_not_delete_record(rdb):
    memory.store_knowledge_entry(rdb, _make_knowledge(key="crashloop"))
    pid = memory.store_pending_suggestion(rdb, _make_pending())
    memory.approve_pending_suggestion(
        rdb, pid, actor="Alice", mode="existing", knowledge_key="crashloop",
        symptom="x", context_notes="")
    assert rdb.sismember(memory.PENDING_INDEX, pid)
    assert memory.get_pending_suggestion(rdb, pid) is not None


def test_approve_pending_suggestion_missing_returns_none(rdb):
    assert memory.approve_pending_suggestion(
        rdb, "does-not-exist", actor="Alice", mode="existing",
        knowledge_key="crashloop", symptom="x", context_notes="") is None


def test_reject_pending_suggestion_sets_status_actor_and_reason(rdb):
    pid = memory.store_pending_suggestion(rdb, _make_pending())
    ok = memory.reject_pending_suggestion(rdb, pid, actor="Bob", decision_reason="not applicable")
    assert ok is True
    entry = memory.get_pending_suggestion(rdb, pid)
    assert entry["status"] == "rejected"
    assert entry["decided_by"] == "Bob"
    assert entry["decision_reason"] == "not applicable"


def test_reject_pending_suggestion_does_not_delete_record(rdb):
    pid = memory.store_pending_suggestion(rdb, _make_pending())
    memory.reject_pending_suggestion(rdb, pid, actor="Bob")
    assert rdb.sismember(memory.PENDING_INDEX, pid)


def test_reject_pending_suggestion_missing_returns_false(rdb):
    assert memory.reject_pending_suggestion(rdb, "does-not-exist", actor="Bob") is False


def test_record_incident_occurrence_creates_hash(rdb):
    iid = memory.record_incident_occurrence(rdb, _make_occurrence())
    assert rdb.hexists(f"incident:{iid}", "alert_name")
    assert rdb.sismember("incidents:index", iid)
    assert rdb.sismember(memory.OPEN_INDEX, iid)


def test_record_incident_occurrence_does_not_write_embedding(rdb):
    iid = memory.record_incident_occurrence(rdb, _make_occurrence())
    assert rdb.hget(f"incident:{iid}", "embedding") is None


def test_score_all_returns_score_and_item_tuple(rdb):
    memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="HighErrorRate", service="ride-service"))
    scored = memory._score_all(rdb, "HighErrorRate")
    assert len(scored) == 1
    score, item = scored[0]
    assert isinstance(score, float)
    assert score == 1.0
    assert item["alert_name"] == "HighErrorRate"


def test_score_all_normalizes_relative_to_top_match(rdb):
    # Distinct `service` values so U2's merge-or-create logic (same service + alert_name
    # merges into one incident) doesn't collapse these into a single record — this test
    # is about BM25 relative scoring, independent of the merge behavior.
    memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="KubePodNotReady", service="ride-service", waiting_reason="CrashLoopBackOff",
        log_error="postgres timeout"))
    memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="KubePodNotReady", service="dispatch-service", waiting_reason="OOMKilled",
        log_error="memory exceeded"))
    scored = memory._score_all(rdb, "KubePodNotReady CrashLoopBackOff postgres timeout")
    assert scored[0][0] == 1.0
    assert 0.0 < scored[1][0] < 1.0


def test_score_all_excludes_zero_overlap_candidates(rdb):
    memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="HighErrorRate"))
    scored = memory._score_all(rdb, "completely unrelated query text xyz")
    assert scored == []


def test_score_all_empty_corpus_returns_empty(rdb):
    assert memory._score_all(rdb, "any query") == []


def test_diversify_keeps_highest_score_per_signature():
    item_a = {"service": "ride-service", "alert_name": "HighErrorRate", "waiting_reason": "CrashLoopBackOff"}
    item_b = {"service": "ride-service", "alert_name": "HighErrorRate", "waiting_reason": "CrashLoopBackOff"}
    item_c = {"service": "dispatch-service", "alert_name": "PodCrash", "waiting_reason": "OOMKilled"}
    scored = [(0.9, item_a), (0.7, item_b), (0.5, item_c)]

    result = memory._diversify(scored, top_k=3)

    assert len(result) == 2
    assert result[0] == (0.9, item_a)
    assert result[1] == (0.5, item_c)


def test_diversify_keeps_different_waiting_reasons_separate():
    # Regression: waiting_reason must be a discriminating field in the diversify key.
    # Two incidents with identical service + alert_name but DIFFERENT waiting_reason
    # must NOT be collapsed by _diversify() — both must survive in the result.
    item_a = {
        "service": "ride-service",
        "alert_name": "HighErrorRate",
        "waiting_reason": "CrashLoopBackOff"
    }
    item_b = {
        "service": "ride-service",
        "alert_name": "HighErrorRate",
        "waiting_reason": "OOMKilled"
    }
    scored = [(0.9, item_a), (0.8, item_b)]

    result = memory._diversify(scored, top_k=3)

    # Both must survive because waiting_reason is different
    assert len(result) == 2
    assert result[0] == (0.9, item_a)
    assert result[1] == (0.8, item_b)


def test_search_memory_items_returns_score_and_fields(rdb):
    memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="HighErrorRate", service="ride-service"))
    items = memory.search_memory_items(rdb, "HighErrorRate ride-service")
    assert len(items) == 1
    assert items[0]["alert_name"] == "HighErrorRate"
    assert items[0]["service"] == "ride-service"
    assert isinstance(items[0]["score"], float)


def test_search_memory_items_empty_store_returns_empty_list(rdb):
    assert memory.search_memory_items(rdb, "anything") == []


def test_search_memory_items_respects_limit(rdb):
    for i in range(5):
        memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="HighErrorRate", service=f"svc-{i}"))
    items = memory.search_memory_items(rdb, "HighErrorRate", limit=2)
    assert len(items) == 2


def test_format_incidents_renders_expected_line_format():
    items = [{"alert_name": "HighErrorRate", "service": "ride-service",
              "root_cause": "dispatch consumer stale cursor",
              "kubectl_hint": "kubectl rollout restart deployment/dispatch-service -n vroom-dev",
              "score": 0.85}]
    result = memory.format_incidents(items)
    assert result == (
        "[1] (similarity: 0.85) HighErrorRate on ride-service → "
        "root cause: dispatch consumer stale cursor → "
        "kubectl rollout restart deployment/dispatch-service -n vroom-dev"
    )


def test_format_incidents_shows_no_action_when_kubectl_hint_missing():
    items = [{"alert_name": "HighErrorRate", "service": "ride-service",
              "root_cause": "unknown", "kubectl_hint": "", "score": 0.5}]
    result = memory.format_incidents(items)
    assert "no action" in result


def test_search_memory_empty_query_no_crash(rdb):
    memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="HighErrorRate"))
    result = memory.search_memory(rdb, "")
    assert result == "no relevant memory found"


def test_service_not_part_of_scored_text(rdb):
    # D2 regression: service must not leak into the corpus-side scored text, even
    # if a caller's query string happens to literally mention a service name.
    # The two incidents deliberately use DIFFERENT alert_names (not both
    # "PodNotReady") so the wrong-cause incident has zero legitimate overlap with
    # the query once service is excluded — under the regressed (service-included)
    # design it would still match on "ride"/"service" and survive the floor,
    # making `len(scored) == 1` the assertion that actually distinguishes the two
    # designs (checking only scored[0]'s identity can degrade to a coin-flip tie).
    memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="Unrelated", service="ride-service",
        waiting_reason="OOMKilled", log_error="memory limit exceeded"))
    memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="PodNotReady", service="dispatch-service",
        waiting_reason="CrashLoopBackOff", log_error="connection refused unreachable"))

    scored = memory._score_all(rdb, "ride-service PodNotReady CrashLoopBackOff")
    assert len(scored) == 1
    assert scored[0][1]["service"] == "dispatch-service"


def test_search_memory_returns_no_match_on_empty_store(rdb):
    result = memory.search_memory(rdb, "HighErrorRate ride-service")
    assert result == "no relevant memory found"


def test_search_memory_returns_formatted_text(rdb):
    memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="HighErrorRate", service="ride-service",
        root_cause="dispatch consumer stale cursor",
    ))
    result = memory.search_memory(rdb, "HighErrorRate ride-service")
    assert "HighErrorRate" in result
    assert "dispatch consumer stale cursor" in result


def test_search_memory_returns_no_match_for_unrelated_query(rdb):
    # Store an incident about an unrelated topic
    memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="OOMKilled", service="notification-service",
        symptoms="node memory pressure", root_cause="connection pool leak"
    ))
    # Add a few more incidents so the store isn't trivially small
    for i in range(4):
        memory.record_incident_occurrence(rdb, _make_occurrence(
            alert_name=f"Alert{i}", service="ride-service",
            symptoms="test symptom", root_cause=f"root cause {i}"
        ))
    result = memory.search_memory(rdb, "certificate expiry TLS handshake failure")
    # Either no match found or something returned — key assertion is it does not crash
    assert isinstance(result, str)


def test_recalibrate_thresholds_removed():
    assert not hasattr(memory, "recalibrate_thresholds")


def test_search_memory_excludes_zero_overlap_candidates(rdb):
    # A genuinely unrelated incident must not appear in results — there is no
    # recency/outcome blend to let it "launder" through, and BM25 excludes
    # anything with zero shared tokens by construction (the floor, D4).
    memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="Unrelated", service="unrelated-service"))
    result = memory.search_memory(rdb, "completely different query text")
    assert result == "no relevant memory found"


def test_search_memory_output_includes_similarity_score(rdb):
    memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="HighErrorRate", service="ride-service"))
    result = memory.search_memory(rdb, "HighErrorRate ride-service")
    assert "(similarity:" in result


def test_search_memory_shows_kubectl_hint(rdb):
    memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="HighErrorRate", service="ride-service",
        kubectl_hint="kubectl rollout restart deployment/ride-service -n vroom-dev"))
    result = memory.search_memory(rdb, "HighErrorRate ride-service")
    assert "kubectl rollout restart deployment/ride-service -n vroom-dev" in result


def test_search_memory_does_not_show_status_or_actor_fields(rdb):
    memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="HighErrorRate", service="ride-service"))
    result = memory.search_memory(rdb, "HighErrorRate ride-service")
    assert "open" not in result
    assert "resolved" not in result


def test_search_memory_diversifies_duplicate_signature(rdb):
    memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="HighErrorRate", service="ride-service",
        waiting_reason="CrashLoopBackOff", log_error="postgres timeout"))
    memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="HighErrorRate", service="ride-service",
        waiting_reason="CrashLoopBackOff", log_error="postgres timeout"))
    memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="PodCrash", service="dispatch-service",
        waiting_reason="OOMKilled", log_error="memory exceeded"))

    result = memory.search_memory(
        rdb, "HighErrorRate CrashLoopBackOff postgres timeout PodCrash OOMKilled memory exceeded")
    lines = [l for l in result.splitlines() if l.strip()]
    assert len(lines) == 2


def test_old_runbook_and_dedup_functions_removed():
    assert not hasattr(memory, "store_runbook_entry")
    assert not hasattr(memory, "get_runbook_entries")
    assert not hasattr(memory, "search_runbook")
    assert not hasattr(memory, "dedupe_against_runbook")
    assert not hasattr(memory, "_is_same_lesson")
    assert not hasattr(memory, "RUNBOOK_INDEX")


# ── Incident lifecycle (U2 merge-or-create) ───────────────────────────────────

def test_record_incident_occurrence_creates_new_when_none_open(rdb):
    iid = memory.record_incident_occurrence(rdb, _make_occurrence())
    assert rdb.sismember("incidents:index", iid)
    assert rdb.sismember(memory.OPEN_INDEX, iid)


def test_record_incident_occurrence_merges_into_existing_open_incident(rdb):
    iid1 = memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="HighErrorRate", service="ride-service", waiting_reason="CrashLoopBackOff"))
    iid2 = memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="HighErrorRate", service="ride-service", waiting_reason="OOMKilled"))
    assert iid1 == iid2
    assert rdb.scard(memory.OPEN_INDEX) == 1
    updated = memory.get_incident(rdb, iid1)
    assert updated["waiting_reason"] == "OOMKilled"


def test_record_incident_occurrence_does_not_merge_different_service(rdb):
    iid1 = memory.record_incident_occurrence(rdb, _make_occurrence(service="ride-service"))
    iid2 = memory.record_incident_occurrence(rdb, _make_occurrence(service="dispatch-service"))
    assert iid1 != iid2


def test_record_incident_occurrence_does_not_merge_different_alert_name(rdb):
    iid1 = memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="HighErrorRate"))
    iid2 = memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="PodCrash"))
    assert iid1 != iid2


def test_record_incident_occurrence_appends_fired_timeline_entry(rdb):
    iid = memory.record_incident_occurrence(rdb, _make_occurrence())
    timeline = memory.get_incident_timeline(rdb, iid)
    assert len(timeline) == 1
    assert timeline[0]["type"] == "fired"
    assert "timestamp" in timeline[0]
    assert "evidence_snapshot" in timeline[0]


def test_record_incident_occurrence_second_fire_appends_second_timeline_entry(rdb):
    iid = memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="A", service="ride"))
    memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="A", service="ride"))
    assert len(memory.get_incident_timeline(rdb, iid)) == 2


def test_get_incident_includes_template_diff_when_present(rdb):
    iid = memory.record_incident_occurrence(rdb, _make_occurrence(template_diff={
        "image_changed": False, "old_image": "", "new_image": "",
        "env_changed": True,
        "env_diff": [{"key": "REDIS_ADDR", "old_value": "redis:6379", "new_value": "bad-host:6379"}],
        "changed_at": "2026-07-07T00:00:00Z",
    }))
    incident = memory.get_incident(rdb, iid)
    assert incident["template_diff"]["env_diff"][0]["key"] == "REDIS_ADDR"


def test_get_incident_template_diff_none_when_absent(rdb):
    iid = memory.record_incident_occurrence(rdb, _make_occurrence())
    incident = memory.get_incident(rdb, iid)
    assert incident["template_diff"] is None


def test_get_incident_includes_dependency_when_present(rdb):
    iid = memory.record_incident_occurrence(rdb, _make_occurrence(dependency={
        "name": "postgres", "namespace": "vroom-dev",
        "pods_available": 0, "pods_desired": 1, "waiting_reason": "CrashLoopBackOff",
    }))
    incident = memory.get_incident(rdb, iid)
    assert incident["dependency"]["name"] == "postgres"


def test_get_incident_dependency_none_when_absent(rdb):
    iid = memory.record_incident_occurrence(rdb, _make_occurrence())
    incident = memory.get_incident(rdb, iid)
    assert incident["dependency"] is None


def test_record_incident_occurrence_merge_updates_template_diff(rdb):
    iid1 = memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="A", service="ride", template_diff=None))
    iid2 = memory.record_incident_occurrence(rdb, _make_occurrence(
        alert_name="A", service="ride", template_diff={
            "image_changed": True, "old_image": "v1", "new_image": "v2",
            "env_changed": False, "env_diff": [], "changed_at": "now",
        }))
    assert iid1 == iid2
    updated = memory.get_incident(rdb, iid1)
    assert updated["template_diff"]["new_image"] == "v2"


def test_old_store_incident_removed():
    assert not hasattr(memory, "store_incident")


# ── Incident read-side + resolve ──────────────────────────────────────────────

def test_get_incident_returns_none_when_missing(rdb):
    assert memory.get_incident(rdb, "does-not-exist") is None


def test_get_incident_includes_id_and_typed_fields(rdb):
    iid = memory.record_incident_occurrence(rdb, _make_occurrence(pods_available=0, pods_desired=3))
    incident = memory.get_incident(rdb, iid)
    assert incident["id"] == iid
    assert incident["pods_available"] == 0
    assert incident["pods_desired"] == 3
    assert incident["low_confidence"] is False
    assert incident["status"] == "open"


def test_list_incidents_filters_by_status(rdb):
    iid_open     = memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="A"))
    iid_resolved = memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="B"))
    memory.resolve_incident(rdb, iid_resolved, "Alice")
    open_only     = memory.list_incidents(rdb, status="open")
    resolved_only = memory.list_incidents(rdb, status="resolved")
    assert [i["id"] for i in open_only]     == [iid_open]
    assert [i["id"] for i in resolved_only] == [iid_resolved]


def test_list_incidents_no_filter_returns_all(rdb):
    memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="A"))
    memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="B"))
    assert len(memory.list_incidents(rdb)) == 2


def test_get_latest_incident_returns_none_when_empty(rdb):
    assert memory.get_latest_incident(rdb) is None


def test_get_latest_incident_picks_most_recent_activity(rdb):
    iid_a = memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="A", service="ride"))
    memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="B", service="dispatch"))
    memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="A", service="ride"))
    latest = memory.get_latest_incident(rdb)
    assert latest["id"] == iid_a


def test_resolve_incident_sets_status_actor_and_removes_from_open_index(rdb):
    iid = memory.record_incident_occurrence(rdb, _make_occurrence())
    ok = memory.resolve_incident(rdb, iid, "Alice")
    assert ok is True
    incident = memory.get_incident(rdb, iid)
    assert incident["status"] == "resolved"
    assert incident["resolved_by"] == "Alice"
    assert incident["resolved_at"] != ""
    assert not rdb.sismember(memory.OPEN_INDEX, iid)


def test_resolve_incident_appends_resolved_timeline_entry(rdb):
    iid = memory.record_incident_occurrence(rdb, _make_occurrence())
    memory.resolve_incident(rdb, iid, "Alice")
    timeline = memory.get_incident_timeline(rdb, iid)
    assert timeline[-1]["type"] == "resolved"
    assert timeline[-1]["actor"] == "Alice"


def test_resolve_incident_missing_returns_false(rdb):
    assert memory.resolve_incident(rdb, "does-not-exist", "Alice") is False


def test_resolve_incident_does_not_prevent_new_incident_for_same_alert(rdb):
    iid1 = memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="A", service="ride"))
    memory.resolve_incident(rdb, iid1, "Alice")
    iid2 = memory.record_incident_occurrence(rdb, _make_occurrence(alert_name="A", service="ride"))
    assert iid1 != iid2
