from stores.incidents import IncidentStore
from stores.knowledge import KnowledgeStore


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.lists = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def lpush(self, key, *values):
        self.lists.setdefault(key, [])[:0] = values

    def lrange(self, key, start, end):
        values = self.lists.get(key, [])
        return values[start:] if end == -1 else values[start:end + 1]

    def ltrim(self, key, start, end):
        self.lists[key] = self.lrange(key, start, end)


def test_incident_store_assigns_an_id_and_reads_the_saved_record():
    store = IncidentStore(FakeRedis())

    saved = store.save({"alert": {"service": "orders"}})

    assert saved["id"]
    assert saved["created_at"]
    assert store.get(saved["id"])["alert"]["service"] == "orders"


def test_incident_store_lists_newest_records_first():
    store = IncidentStore(FakeRedis())
    first = store.save({"alert": {"service": "orders"}})
    second = store.save({"alert": {"service": "payments"}})

    assert [record["id"] for record in store.list()] == [second["id"], first["id"]]


def test_knowledge_store_round_trips_approved_examples():
    store = KnowledgeStore(FakeRedis())
    knowledge = {"families": [], "examples": [{"id": "redis-down", "approved": True}], "hints": []}

    store.save(knowledge)

    assert store.load() == knowledge


def test_knowledge_store_returns_an_empty_structure_before_first_save():
    assert KnowledgeStore(FakeRedis()).load() == {"families": [], "examples": [], "hints": []}


def test_knowledge_store_builds_evidence_only_documents_and_finds_the_family():
    store = KnowledgeStore(FakeRedis())
    store.save({
        "families": [{
            "knowledge_key": "redis_connection",
            "diagnosis_cause": "Redis endpoint is unavailable.",
            "remediation": "Restore Redis.",
        }],
        "examples": [{
            "example_id": "example-1",
            "knowledge_key": "redis_connection",
            "fingerprint": "fingerprint-1",
            "exact_reusable": True,
            "evidence": {"alert_name": "PodCrashLoop", "service": "orders", "log_error": "connection refused"},
            "hint_ids": ["hint-1"],
        }],
        "hints": [{"hint_id": "hint-1", "text": "check REDIS_ADDR"}],
    })

    corpus = store.corpus()
    document = corpus.get_documents()[0]

    assert document["knowledge_key"] == "redis_connection"
    assert document["evidence_text"].startswith("alert_name: PodCrashLoop")
    assert document["hint_texts"] == ["check REDIS_ADDR"]
    assert corpus.knowledge("redis_connection")["remediation"] == "Restore Redis."
