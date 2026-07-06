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
    assert len(ride_entries) > 0


def test_seed_extracts_scale_command():
    entries = seed_mod._parse_vroom_ops(os.path.join(RUNBOOKS_DIR, "vroom-ops.md"))
    scale_entries = [e for e in entries if "scale" in e.get("fix_action", "")]
    assert len(scale_entries) > 0
    for e in scale_entries:
        assert "kubectl scale" in e["fix_action"]


def test_slugify_lowercases_and_replaces_non_alnum():
    assert seed_mod._slugify("Outbox not draining") == "outbox_not_draining"


def test_slugify_strips_leading_trailing_underscores():
    assert seed_mod._slugify("  Pod OOMKilled!  ") == "pod_oomkilled"


def test_seed_if_empty_creates_8_bootstrap_knowledge_entries(rdb):
    seed_mod.seed_if_empty(rdb, RUNBOOKS_DIR)
    for key in ("init_oom", "init_crashloop", "oom", "crashloop",
                "image_pull", "config_error", "failed_scheduling", "zero_replica"):
        entry = memory.get_knowledge_entry(rdb, key)
        assert entry is not None, f"expected bootstrap key {key!r}"
        assert entry["source"] == "bootstrap"


def test_seed_if_empty_sets_conclusive_per_d3_table(rdb):
    seed_mod.seed_if_empty(rdb, RUNBOOKS_DIR)
    assert memory.get_knowledge_entry(rdb, "oom")["conclusive"] is True
    assert memory.get_knowledge_entry(rdb, "init_oom")["conclusive"] is True
    assert memory.get_knowledge_entry(rdb, "config_error")["conclusive"] is True
    assert memory.get_knowledge_entry(rdb, "crashloop")["conclusive"] is False
    assert memory.get_knowledge_entry(rdb, "zero_replica")["conclusive"] is False


def test_seed_if_empty_collapses_oom_duplicate_from_vroom_ops(rdb):
    seed_mod.seed_if_empty(rdb, RUNBOOKS_DIR)
    # "Pod OOMKilled" section must NOT create a second knowledge entry —
    # it becomes a history entry under the existing "oom" key instead.
    assert memory.get_knowledge_entry(rdb, "pod_oomkilled") is None
    oom_history = memory.list_history_entries_for_knowledge(rdb, "oom")
    assert len(oom_history) >= 1


def test_seed_if_empty_creates_history_for_every_vroom_ops_section(rdb):
    n_sections = len(seed_mod._parse_vroom_ops(os.path.join(RUNBOOKS_DIR, "vroom-ops.md")))
    seed_mod.seed_if_empty(rdb, RUNBOOKS_DIR)
    assert len(memory.list_all_history_entries(rdb)) == n_sections


def test_seed_if_empty_skips_when_knowledge_not_empty(rdb):
    memory.store_knowledge_entry(rdb, {
        "key": "existing", "root_cause_pattern": "x", "fix_action": "y",
        "trigger_waiting_reason": "", "conclusive": False, "source": "bootstrap", "created_by": "bootstrap",
    })
    n = seed_mod.seed_if_empty(rdb, RUNBOOKS_DIR)
    assert n == 0


def test_seed_does_not_touch_incidents_index(rdb):
    seed_mod.seed_if_empty(rdb, RUNBOOKS_DIR)
    assert rdb.scard("incidents:index") == 0
