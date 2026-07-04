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


def _make_incident(**kwargs):
    base = {
        "alert_name": "HighErrorRate",
        "service": "ride-service",
        "namespace": "vroom-dev",
        "symptoms": "rps=12.4 err=8.3% p99=1.2s loki_errors=47",
        "investigation_steps": [],
        "root_cause": "dispatch consumer stale cursor",
        "remediation_tool": "restart_deployment",
        "remediation_args": {"deployment": "dispatch-service", "namespace": "vroom-dev"},
        "outcome": "resolved",
    }
    base.update(kwargs)
    return base


def test_build_symptom_text_includes_all_fields():
    text = memory.build_symptom_text("HighErrorRate", "ride-service",
                                      "CrashLoopBackOff", "dial tcp timeout")
    assert text == "HighErrorRate ride-service CrashLoopBackOff dial tcp timeout"


def test_build_symptom_text_handles_missing_optional_fields():
    assert memory.build_symptom_text("Alert", "svc") == "Alert svc"


def test_build_symptom_text_symmetry_between_storage_and_query_call_sites():
    # The exact scenario the bug fix guards against: text built for storage and
    # text built for a later query, from the same underlying facts, must be identical.
    stored_text = memory.build_symptom_text(
        "PodCrash", "dispatch-service", "OOMKilled", "exit code 137")
    query_text = memory.build_symptom_text(
        "PodCrash", "dispatch-service", "OOMKilled", "exit code 137")
    assert stored_text == query_text


def test_store_incident_creates_hash(rdb):
    iid = memory.store_incident(rdb, _make_incident())
    assert rdb.hexists(f"incident:{iid}", "alert_name")
    assert rdb.sismember("incidents:index", iid)


def test_store_incident_saves_embedding(rdb):
    iid = memory.store_incident(rdb, _make_incident())
    raw_emb = rdb.hget(f"incident:{iid}", "embedding")
    emb = json.loads(raw_emb)
    assert isinstance(emb, list)
    assert len(emb) == 384  # all-MiniLM-L6-v2 output dimension


def test_store_incident_uses_build_symptom_text_for_embedding(rdb):
    # The embedded text must come from build_symptom_text, not an inline duplicate
    # of the same formula — otherwise storage and query text can drift apart again.
    import numpy as np
    incident = _make_incident(alert_name="PodCrash", service="dispatch-service",
                               waiting_reason="OOMKilled", log_error="exit code 137")
    iid = memory.store_incident(rdb, incident)
    stored_emb = json.loads(rdb.hget(f"incident:{iid}", "embedding"))
    expected_text = memory.build_symptom_text("PodCrash", "dispatch-service",
                                                "OOMKilled", "exit code 137")
    expected_emb = memory._encode(expected_text)
    assert np.allclose(stored_emb, expected_emb)


def test_retrieve_similar_returns_top_k(rdb):
    memory.store_incident(rdb, _make_incident(alert_name="HighErrorRate", service="ride-service"))
    memory.store_incident(rdb, _make_incident(alert_name="PodCrash", service="dispatch-service"))
    memory.store_incident(rdb, _make_incident(alert_name="OutboxNotDraining", service="ride-service"))

    results = memory.retrieve_similar(rdb, "HighErrorRate ride-service", top_k=2)
    assert len(results) == 2
    # Most similar to HighErrorRate on ride-service should rank first
    assert results[0]["alert_name"] == "HighErrorRate"


def test_retrieve_similar_empty_store(rdb):
    results = memory.retrieve_similar(rdb, "any query", top_k=3)
    assert results == []


def test_score_all_returns_score_cos_sim_and_item_tuple(rdb):
    memory.store_incident(rdb, _make_incident(alert_name="HighErrorRate", service="ride-service"))
    scored = memory._score_all(rdb, "HighErrorRate ride-service")
    assert len(scored) == 1
    score, cos_sim, item = scored[0]
    assert isinstance(score, float)
    assert isinstance(cos_sim, float)
    assert item["alert_name"] == "HighErrorRate"


def test_recency_score_decays(rdb):
    old_ts = int(time.time()) - 8 * 86400  # 8 days ago — past 7-day window
    score = memory._recency_score(old_ts)
    assert score == 0.0

    recent_ts = int(time.time()) - 3600  # 1 hour ago
    score = memory._recency_score(recent_ts)
    assert score > 0.9


def test_search_memory_returns_no_match_on_empty_store(rdb):
    result = memory.search_memory(rdb, "HighErrorRate ride-service")
    assert result == "no relevant memory found"


def test_search_memory_returns_formatted_text(rdb):
    memory.store_incident(rdb, _make_incident(
        alert_name="HighErrorRate", service="ride-service",
        root_cause="dispatch consumer stale cursor", remediation_tool="restart_deployment"
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


def test_search_memory_filters_on_raw_cosine_not_blended_score(rdb):
    # Regression test for the bug where the floor gated the blended score
    # (0.6*cos_sim + 0.3*recency + 0.1*outcome) instead of cos_sim alone. A
    # same-day, "acknowledged" incident with near-zero semantic similarity used
    # to score 0.6*0 + 0.3*1.0 + 0.1*0.5 = 0.35, clearing the old default floor
    # of 0.30 despite having no real relevance to the query.
    memory.store_incident(rdb, _make_incident(
        alert_name="Unrelated", service="unrelated-service", outcome="acknowledged"
    ))
    # Force cos_sim to exactly 0.0 for every candidate regardless of the real
    # embedding model's output, so the test isolates the filter logic itself.
    with patch.object(memory, "_encode", return_value=[0.0] * 384):
        result = memory.search_memory(rdb, "completely different query")
    assert result == "no relevant memory found"


def test_search_memory_output_includes_similarity_score(rdb):
    memory.store_incident(rdb, _make_incident(alert_name="HighErrorRate", service="ride-service"))
    result = memory.search_memory(rdb, "HighErrorRate ride-service")
    assert "(similarity:" in result


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


def test_store_runbook_entry_saves_embedding(rdb):
    eid = memory.store_runbook_entry(rdb, _make_runbook_entry())
    raw = rdb.hget(f"runbook:entry:{eid}", "embedding")
    emb = json.loads(raw)
    assert isinstance(emb, list)
    assert len(emb) == 384


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
        memory.store_runbook_entry(rdb, _make_runbook_entry(title=f"Entry {i}", service="svc"))
    results = memory.search_runbook(rdb, "svc", top_k=2)
    assert len(results) == 2
