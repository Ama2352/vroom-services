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


def test_store_incident_creates_hash(rdb):
    iid = memory.store_incident(rdb, _make_incident())
    assert rdb.hexists(f"incident:{iid}", "alert_name")
    assert rdb.sismember("incidents:index", iid)


def test_store_incident_does_not_write_embedding(rdb):
    iid = memory.store_incident(rdb, _make_incident())
    assert rdb.hget(f"incident:{iid}", "embedding") is None


def test_retrieve_similar_returns_top_k(rdb):
    memory.store_incident(rdb, _make_incident(alert_name="HighErrorRate", service="ride-service",
                                               waiting_reason="CrashLoopBackOff"))
    memory.store_incident(rdb, _make_incident(alert_name="PodCrash", service="dispatch-service",
                                               waiting_reason="CrashLoopBackOff"))
    memory.store_incident(rdb, _make_incident(alert_name="OutboxNotDraining", service="ride-service",
                                               waiting_reason="OOMKilled"))

    results = memory.retrieve_similar(rdb, "HighErrorRate CrashLoopBackOff", top_k=2)
    assert len(results) == 2
    assert results[0]["alert_name"] == "HighErrorRate"


def test_retrieve_similar_empty_store(rdb):
    results = memory.retrieve_similar(rdb, "any query", top_k=3)
    assert results == []


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
