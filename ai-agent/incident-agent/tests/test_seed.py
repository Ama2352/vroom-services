import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
try:
    import fakeredis
except ImportError:
    pytest.skip("fakeredis not installed", allow_module_level=True)

import memory
import seed as seed_mod

RUNBOOKS_DIR = os.path.join(os.path.dirname(__file__), "..", "runbooks")


@pytest.fixture
def rdb():
    return fakeredis.FakeRedis()


def test_parse_vroom_ops_returns_list():
    entries = seed_mod._parse_vroom_ops(os.path.join(RUNBOOKS_DIR, "vroom-ops.md"))
    assert isinstance(entries, list)
    assert len(entries) > 0


def test_seed_extracts_service_from_title():
    entries = seed_mod._parse_vroom_ops(os.path.join(RUNBOOKS_DIR, "vroom-ops.md"))
    ride_entries = [e for e in entries if "ride-service" in e.get("service", "")]
    assert len(ride_entries) > 0, "Expected at least one entry with service containing ride-service"


def test_seed_extracts_scale_command():
    entries = seed_mod._parse_vroom_ops(os.path.join(RUNBOOKS_DIR, "vroom-ops.md"))
    scale_entries = [e for e in entries if "scale" in e.get("fix_command", "")]
    assert len(scale_entries) > 0, "Expected at least one scale entry from vroom-ops.md"
    for e in scale_entries:
        assert "kubectl scale" in e["fix_command"]


def test_seed_all_entries_have_bootstrap_source():
    entries = seed_mod._parse_vroom_ops(os.path.join(RUNBOOKS_DIR, "vroom-ops.md"))
    assert all(e.get("source") == "bootstrap" for e in entries)


def test_seed_if_empty_stores_to_runbook_index(rdb):
    n = seed_mod.seed_if_empty(rdb, RUNBOOKS_DIR)
    assert n > 0
    assert rdb.scard(memory.RUNBOOK_INDEX) == n


def test_seed_if_empty_skips_when_runbook_not_empty(rdb):
    memory.store_runbook_entry(rdb, {
        "title": "existing", "service": "test",
        "symptom": "", "root_cause": "", "fix_command": "", "source": "bootstrap"
    })
    n = seed_mod.seed_if_empty(rdb, RUNBOOKS_DIR)
    assert n == 0


def test_seed_does_not_touch_incidents_index(rdb):
    seed_mod.seed_if_empty(rdb, RUNBOOKS_DIR)
    assert rdb.scard("incidents:index") == 0
