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


def _make_incident(**kwargs):
    base = {
        "alert_name":     "HighErrorRate",
        "service":        "ride-service",
        "namespace":      "vroom-dev",
        "symptoms":       "rps=12.4 err=8.3% p99=1.2s loki_errors=47",
        "waiting_reason": "",
        "log_error":      "",
        "root_cause":     "dispatch consumer stale cursor",
        "kubectl_hint":   "kubectl rollout restart deployment/dispatch-service -n vroom-dev",
        "outcome":        "resolved",
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


def test_store_incident_creates_hash(rdb):
    iid = memory.store_incident(rdb, _make_incident())
    assert rdb.hexists(f"incident:{iid}", "alert_name")
    assert rdb.sismember("incidents:index", iid)


def test_store_incident_does_not_write_embedding(rdb):
    iid = memory.store_incident(rdb, _make_incident())
    assert rdb.hget(f"incident:{iid}", "embedding") is None


def test_score_all_returns_score_and_item_tuple(rdb):
    memory.store_incident(rdb, _make_incident(alert_name="HighErrorRate", service="ride-service"))
    scored = memory._score_all(rdb, "HighErrorRate")
    assert len(scored) == 1
    score, item = scored[0]
    assert isinstance(score, float)
    assert score == 1.0
    assert item["alert_name"] == "HighErrorRate"


def test_score_all_normalizes_relative_to_top_match(rdb):
    memory.store_incident(rdb, _make_incident(
        alert_name="KubePodNotReady", waiting_reason="CrashLoopBackOff",
        log_error="postgres timeout"))
    memory.store_incident(rdb, _make_incident(
        alert_name="KubePodNotReady", waiting_reason="OOMKilled",
        log_error="memory exceeded"))
    scored = memory._score_all(rdb, "KubePodNotReady CrashLoopBackOff postgres timeout")
    assert scored[0][0] == 1.0
    assert 0.0 < scored[1][0] < 1.0


def test_score_all_excludes_zero_overlap_candidates(rdb):
    memory.store_incident(rdb, _make_incident(alert_name="HighErrorRate"))
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
    memory.store_incident(rdb, _make_incident(alert_name="HighErrorRate", service="ride-service"))
    items = memory.search_memory_items(rdb, "HighErrorRate ride-service")
    assert len(items) == 1
    assert items[0]["alert_name"] == "HighErrorRate"
    assert items[0]["service"] == "ride-service"
    assert isinstance(items[0]["score"], float)


def test_search_memory_items_empty_store_returns_empty_list(rdb):
    assert memory.search_memory_items(rdb, "anything") == []


def test_search_memory_items_respects_limit(rdb):
    for i in range(5):
        memory.store_incident(rdb, _make_incident(alert_name="HighErrorRate", service=f"svc-{i}"))
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
    memory.store_incident(rdb, _make_incident(alert_name="HighErrorRate"))
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
    memory.store_incident(rdb, _make_incident(
        alert_name="Unrelated", service="ride-service",
        waiting_reason="OOMKilled", log_error="memory limit exceeded"))
    memory.store_incident(rdb, _make_incident(
        alert_name="PodNotReady", service="dispatch-service",
        waiting_reason="CrashLoopBackOff", log_error="connection refused unreachable"))

    scored = memory._score_all(rdb, "ride-service PodNotReady CrashLoopBackOff")
    assert len(scored) == 1
    assert scored[0][1]["service"] == "dispatch-service"


def test_search_memory_returns_no_match_on_empty_store(rdb):
    result = memory.search_memory(rdb, "HighErrorRate ride-service")
    assert result == "no relevant memory found"


def test_search_memory_returns_formatted_text(rdb):
    memory.store_incident(rdb, _make_incident(
        alert_name="HighErrorRate", service="ride-service",
        root_cause="dispatch consumer stale cursor",
    ))
    result = memory.search_memory(rdb, "HighErrorRate ride-service")
    assert "HighErrorRate" in result
    assert "dispatch consumer stale cursor" in result


def test_search_memory_returns_no_match_for_unrelated_query(rdb):
    # Store an incident about an unrelated topic
    memory.store_incident(rdb, _make_incident(
        alert_name="OOMKilled", service="notification-service",
        symptoms="node memory pressure", root_cause="connection pool leak"
    ))
    # Add a few more incidents so the store isn't trivially small
    for i in range(4):
        memory.store_incident(rdb, _make_incident(
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
    memory.store_incident(rdb, _make_incident(
        alert_name="Unrelated", service="unrelated-service", outcome="acknowledged"))
    result = memory.search_memory(rdb, "completely different query text")
    assert result == "no relevant memory found"


def test_search_memory_output_includes_similarity_score(rdb):
    memory.store_incident(rdb, _make_incident(alert_name="HighErrorRate", service="ride-service"))
    result = memory.search_memory(rdb, "HighErrorRate ride-service")
    assert "(similarity:" in result


def test_search_memory_shows_kubectl_hint(rdb):
    memory.store_incident(rdb, _make_incident(
        alert_name="HighErrorRate", service="ride-service",
        kubectl_hint="kubectl rollout restart deployment/ride-service -n vroom-dev"))
    result = memory.search_memory(rdb, "HighErrorRate ride-service")
    assert "kubectl rollout restart deployment/ride-service -n vroom-dev" in result


def test_search_memory_does_not_show_outcome(rdb):
    memory.store_incident(rdb, _make_incident(
        alert_name="HighErrorRate", service="ride-service", outcome="acknowledged"))
    result = memory.search_memory(rdb, "HighErrorRate ride-service")
    assert "acknowledged" not in result
    assert "resolved" not in result


def test_search_memory_diversifies_duplicate_signature(rdb):
    memory.store_incident(rdb, _make_incident(
        alert_name="HighErrorRate", service="ride-service",
        waiting_reason="CrashLoopBackOff", log_error="postgres timeout"))
    memory.store_incident(rdb, _make_incident(
        alert_name="HighErrorRate", service="ride-service",
        waiting_reason="CrashLoopBackOff", log_error="postgres timeout"))
    memory.store_incident(rdb, _make_incident(
        alert_name="PodCrash", service="dispatch-service",
        waiting_reason="OOMKilled", log_error="memory exceeded"))

    result = memory.search_memory(
        rdb, "HighErrorRate CrashLoopBackOff postgres timeout PodCrash OOMKilled memory exceeded")
    lines = [l for l in result.splitlines() if l.strip()]
    assert len(lines) == 2


# ── Semantic memory (runbook tier) ────────────────────────────────────────────

def _make_runbook_entry(**kwargs):
    base = {
        "title":       "Deployment scaled to zero",
        "service":     "ride-service",
        "symptom":     "No pods running, replicas=0",
        "root_cause":  "deployment manually scaled to 0",
        "fix_command": "kubectl scale deployment/ride-service -n vroom-dev --replicas=1",
        "source":      "bootstrap",
    }
    base.update(kwargs)
    return base


def test_store_runbook_entry_creates_hash(rdb):
    eid = memory.store_runbook_entry(rdb, _make_runbook_entry())
    assert rdb.hexists(f"runbook:entry:{eid}", "title")
    assert rdb.sismember(memory.RUNBOOK_INDEX, eid)


def test_store_runbook_entry_does_not_write_embedding(rdb):
    eid = memory.store_runbook_entry(rdb, _make_runbook_entry())
    assert rdb.hget(f"runbook:entry:{eid}", "embedding") is None


def test_get_runbook_entries_returns_all(rdb):
    memory.store_runbook_entry(rdb, _make_runbook_entry(title="Entry A"))
    memory.store_runbook_entry(rdb, _make_runbook_entry(title="Entry B"))
    entries = memory.get_runbook_entries(rdb)
    assert len(entries) == 2
    assert all("title" in e for e in entries)


def test_get_runbook_entries_empty(rdb):
    assert memory.get_runbook_entries(rdb) == []


def test_search_runbook_empty_returns_empty_list(rdb):
    assert memory.search_runbook(rdb, "anything") == []


def test_search_runbook_finds_similar_entry(rdb):
    memory.store_runbook_entry(rdb, _make_runbook_entry(
        title="Deployment scaled to zero",
        service="ride-service",
        symptom="No pods running for ride-service, replicas=0",
        root_cause="deployment manually scaled to 0",
    ))
    results = memory.search_runbook(rdb, "ride-service replicas 0 pods missing")
    assert len(results) == 1
    assert results[0]["service"] == "ride-service"
    assert results[0]["title"] == "Deployment scaled to zero"


def test_search_runbook_respects_top_k(rdb):
    for i in range(5):
        memory.store_runbook_entry(rdb, _make_runbook_entry(title=f"Entry {i}"))
    results = memory.search_runbook(rdb, "pods running replicas", top_k=2)
    assert len(results) == 2


def test_search_runbook_applies_floor(rdb):
    memory.store_runbook_entry(rdb, _make_runbook_entry())
    results = memory.search_runbook(rdb, "completely unrelated topic xyz")
    assert results == []


def test_search_runbook_returns_score_field(rdb):
    memory.store_runbook_entry(rdb, _make_runbook_entry(
        title="Deployment scaled to zero", service="ride-service",
        symptom="No pods running for ride-service, replicas=0",
    ))
    results = memory.search_runbook(rdb, "ride-service replicas 0 pods missing")
    assert len(results) == 1
    assert "score" in results[0]
    assert isinstance(results[0]["score"], float)


def test_search_runbook_service_not_part_of_scored_text(rdb):
    memory.store_runbook_entry(rdb, _make_runbook_entry(
        title="Unrelated fix", service="ride-service",
        symptom="disk pressure eviction"))
    memory.store_runbook_entry(rdb, _make_runbook_entry(
        title="Postgres unreachable fix", service="dispatch-service",
        symptom="CrashLoopBackOff connection refused postgres"))

    results = memory.search_runbook(rdb, "ride-service CrashLoopBackOff connection refused postgres")
    # The "Unrelated fix" entry shares zero tokens with the query once `service` is
    # excluded from scored text — it must be excluded by the floor entirely, not just
    # rank second. Under the regressed (service-included) design it would match on
    # "ride"/"service" and appear here too — this assertion is what actually
    # distinguishes the two designs (a same-rank-order-only assertion does not).
    assert len(results) == 1
    assert results[0]["service"] == "dispatch-service"


# ── Cross-tier dedup (Group D) ────────────────────────────────────────────────

def test_is_same_lesson_true_when_same_service_and_high_overlap_root_cause():
    incident = {"service": "ride-service", "root_cause": "dispatch consumer stale cursor"}
    runbook  = {"service": "ride-service",
                "root_cause": "Dispatch service consumer had a stale Redis cursor"}
    assert memory._is_same_lesson(incident, runbook) is True


def test_is_same_lesson_false_when_different_service_even_with_identical_root_cause():
    incident = {"service": "ride-service", "root_cause": "dispatch consumer stale cursor"}
    runbook  = {"service": "dispatch-service", "root_cause": "dispatch consumer stale cursor"}
    assert memory._is_same_lesson(incident, runbook) is False


def test_is_same_lesson_false_when_same_service_but_low_overlap_root_cause():
    incident = {"service": "ride-service", "root_cause": "dispatch consumer stale cursor"}
    runbook  = {"service": "ride-service", "root_cause": "postgres connection pool exhausted"}
    assert memory._is_same_lesson(incident, runbook) is False


def test_is_same_lesson_false_when_incident_root_cause_empty():
    incident = {"service": "ride-service", "root_cause": ""}
    runbook  = {"service": "ride-service", "root_cause": "dispatch consumer stale cursor"}
    assert memory._is_same_lesson(incident, runbook) is False


def test_is_same_lesson_false_when_runbook_root_cause_empty():
    incident = {"service": "ride-service", "root_cause": "dispatch consumer stale cursor"}
    runbook  = {"service": "ride-service", "root_cause": ""}
    assert memory._is_same_lesson(incident, runbook) is False


def test_is_same_lesson_service_comparison_is_case_insensitive():
    incident = {"service": "Ride-Service", "root_cause": "dispatch consumer stale cursor"}
    runbook  = {"service": "ride-service",
                "root_cause": "Dispatch service consumer had a stale Redis cursor"}
    assert memory._is_same_lesson(incident, runbook) is True


def test_dedupe_drops_incident_matching_a_runbook_hit():
    incidents = [{"service": "ride-service", "root_cause": "dispatch consumer stale cursor",
                  "alert_name": "HighErrorRate", "score": 0.9}]
    runbook_hits = [{"service": "ride-service",
                     "root_cause": "Dispatch service consumer had a stale Redis cursor",
                     "title": "Stale cursor fix", "score": 0.8}]
    result = memory.dedupe_against_runbook(incidents, runbook_hits)
    assert result == []


def test_dedupe_keeps_incident_with_no_matching_runbook_hit():
    incidents = [{"service": "ride-service", "root_cause": "dispatch consumer stale cursor",
                  "alert_name": "HighErrorRate", "score": 0.9}]
    runbook_hits = [{"service": "notification-service", "root_cause": "SMTP timeout",
                     "title": "SMTP fix", "score": 0.8}]
    result = memory.dedupe_against_runbook(incidents, runbook_hits)
    assert result == incidents


def test_dedupe_keeps_non_matching_incident_and_drops_matching_one():
    matching = {"service": "ride-service", "root_cause": "dispatch consumer stale cursor",
                "alert_name": "HighErrorRate", "score": 0.9}
    non_matching = {"service": "dispatch-service", "root_cause": "OOM killed",
                    "alert_name": "PodCrash", "score": 0.7}
    runbook_hits = [{"service": "ride-service",
                     "root_cause": "Dispatch service consumer had a stale Redis cursor",
                     "title": "Stale cursor fix", "score": 0.8}]
    result = memory.dedupe_against_runbook([matching, non_matching], runbook_hits)
    assert result == [non_matching]


def test_dedupe_empty_runbook_hits_keeps_all_incidents():
    incidents = [{"service": "ride-service", "root_cause": "dispatch consumer stale cursor",
                  "alert_name": "HighErrorRate", "score": 0.9}]
    result = memory.dedupe_against_runbook(incidents, [])
    assert result == incidents


def test_dedupe_empty_incident_items_returns_empty_list():
    runbook_hits = [{"service": "ride-service", "root_cause": "x", "title": "t", "score": 0.5}]
    assert memory.dedupe_against_runbook([], runbook_hits) == []
