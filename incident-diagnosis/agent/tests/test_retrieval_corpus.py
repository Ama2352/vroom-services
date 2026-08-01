import pytest

import memory
from retrieval.corpus import CorpusProvider, CorpusUnavailable, build_snapshot
from retrieval.models import RetrievalDocument


class FailingGetRedis:
    def __init__(self, delegate, fail_version_reads=False):
        self.delegate = delegate
        self.fail_version_reads = fail_version_reads

    def get(self, key):
        if self.fail_version_reads and key == memory.CORPUS_VERSION_KEY:
            raise ConnectionError("redis unavailable")
        return self.delegate.get(key)

    def __getattr__(self, name):
        return getattr(self.delegate, name)


def make_knowledge(key):
    return {
        "key": key,
        "root_cause_pattern": f"{key} root cause",
        "fix_action": f"fix {key}",
        "trigger_waiting_reason": "",
        "conclusive": False,
    }


def make_history(key):
    return {
        "knowledge_key": key,
        "service": "ride",
        "symptom": "manifest tag not found",
        "context_notes": "GitOps set tag bad",
    }


def test_snapshot_builds_parent_and_joined_history_documents(fake_rdb):
    memory.store_knowledge_entry(fake_rdb, {
        "key": "image_pull", "root_cause_pattern": "invalid image reference",
        "fix_action": "correct the tag", "trigger_waiting_reason": "ImagePullBackOff",
        "conclusive": False,
    })
    hid = memory.store_history_entry(fake_rdb, make_history("image_pull"))
    snapshot = build_snapshot(fake_rdb, memory.get_corpus_version(fake_rdb))
    docs = [d for d in snapshot.documents if d.knowledge_key == "image_pull"]
    assert len(docs) == 2
    assert {d.source for d in docs} == {"knowledge", "history"}
    history = next(d for d in docs if d.source == "history")
    assert history.source_id == hid
    assert "manifest tag not found" in history.document_text
    assert "GitOps set tag bad" in history.document_text
    assert "invalid image reference" in history.document_text


def test_orphan_history_is_excluded(fake_rdb):
    memory.store_history_entry(fake_rdb, make_history("missing"))
    snapshot = build_snapshot(fake_rdb, memory.get_corpus_version(fake_rdb))
    assert all(d.knowledge_key != "missing" for d in snapshot.documents)


def test_sparse_approved_knowledge_is_preserved_for_retrieval(fake_rdb):
    memory.store_knowledge_entry(fake_rdb, {
        "key": "malformed", "root_cause_pattern": "", "fix_action": "",
        "trigger_waiting_reason": "OOMKilled", "conclusive": True,
    })
    snapshot = build_snapshot(fake_rdb, memory.get_corpus_version(fake_rdb))
    assert any(d.knowledge_key == "malformed" for d in snapshot.documents)
    assert "OOMKilled" not in snapshot.exact_by_trigger


def test_provider_reuses_snapshot_while_version_is_unchanged(fake_rdb):
    provider = CorpusProvider(fake_rdb)
    first, first_stale = provider.get_snapshot()
    second, second_stale = provider.get_snapshot()
    assert second is first
    assert not first_stale and not second_stale


def test_provider_rebuilds_after_knowledge_mutation(fake_rdb):
    provider = CorpusProvider(fake_rdb)
    first, _ = provider.get_snapshot()
    memory.store_knowledge_entry(fake_rdb, make_knowledge("oom"))
    second, _ = provider.get_snapshot()
    assert second is not first
    assert second.version > first.version


def test_provider_uses_last_snapshot_when_version_check_fails(fake_rdb):
    proxy = FailingGetRedis(fake_rdb)
    provider = CorpusProvider(proxy)
    snapshot, _ = provider.get_snapshot()
    proxy.fail_version_reads = True
    stale, stale_flag = provider.get_snapshot()
    assert stale is snapshot
    assert stale_flag is True


def test_provider_without_snapshot_raises_when_redis_fails(fake_rdb):
    proxy = FailingGetRedis(fake_rdb, fail_version_reads=True)
    with pytest.raises(CorpusUnavailable):
        CorpusProvider(proxy).get_snapshot()
