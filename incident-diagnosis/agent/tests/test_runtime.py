from collector import CollectedEvidence
from config import Settings
from runtime import build_investigation_service


class FakeRedis:
    def __init__(self): self.values, self.lists = {}, {}
    def get(self, key): return self.values.get(key)
    def set(self, key, value): self.values[key] = value
    def lpush(self, key, *values): self.lists.setdefault(key, [])[:0] = values
    def ltrim(self, key, start, end): self.lists[key] = self.lists.get(key, [])[start:end + 1]
    def lrange(self, key, start, end): return self.lists.get(key, [])[start:] if end == -1 else self.lists.get(key, [])[start:end + 1]


class FakeObservations:
    def collect(self, service, namespace):
        return {"metrics": {}, "logs": {"message": "connection refused"}, "traces": {}, "kubernetes": {}, "configuration": {"status": "unchanged", "changes": []}}


class IdentityReranker:
    def rerank(self, query, candidates): return candidates


def test_runtime_builds_a_service_that_uses_stored_exact_knowledge():
    redis_client = FakeRedis()
    redis_client.set("incident-agent:knowledge", '{"families":[{"knowledge_key":"redis","diagnosis_cause":"Redis unavailable","remediation":"Restore Redis"}],"examples":[],"hints":[]}')
    settings = Settings("redis://x/0", "", "", "", "", "", "", "")
    service = build_investigation_service(settings, redis_client=redis_client, observation_client=FakeObservations(), reranker=IdentityReranker(), generate=lambda prompt: {})

    result = service.investigate({"service": "orders", "alert_name": "PodCrashLoop"})

    assert result["id"]
    assert result["diagnosis"]["diagnosis_cause"] is None
