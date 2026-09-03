from collector import CollectedEvidence
from retrieval.models import EvidenceCandidate, EvidenceRetrievalMode, EvidenceRetrievalResult
from services.investigation import InvestigationService


class FakeCollector:
    def __init__(self, collected):
        self.collected = collected

    def collect(self, alert):
        return self.collected


class FakeIncidentStore:
    def __init__(self):
        self.saved = []

    def save(self, record):
        self.saved.append(record)
        return {**record, "id": "incident-1"}


class FailIfCalled:
    def __call__(self, prompt):
        raise AssertionError("The LLM must not run for an exact approved match")


def _raw_evidence():
    return {
        "metrics": {"waiting_reason": "CrashLoopBackOff"},
        "logs": {"message": "connection refused"},
        "traces": {},
        "kubernetes": {},
        "configuration": {"status": "unchanged", "changes": []},
    }


def test_exact_approved_match_bypasses_the_llm_and_is_saved():
    retrieval = EvidenceRetrievalResult(
        EvidenceRetrievalMode.EXACT,
        (EvidenceCandidate("redis_connection", "example-1", "evidence"),),
    )
    store = FakeIncidentStore()
    service = InvestigationService(
        collector=FakeCollector(CollectedEvidence(_raw_evidence(), ())),
        retrieve=lambda template: retrieval,
        find_knowledge=lambda key: {"diagnosis_cause": "Redis endpoint is unavailable.", "remediation": "Restore Redis."},
        generate=FailIfCalled(),
        incident_store=store,
    )

    result = service.investigate({"alert_name": "PodCrashLoop", "service": "orders"})

    assert result["id"] == "incident-1"
    assert result["diagnosis"]["diagnosis_cause"] == "Redis endpoint is unavailable."
    assert store.saved[0]["retrieval"]["mode"] == "exact"


def test_unavailable_llm_returns_a_saved_evidence_only_diagnosis():
    retrieval = EvidenceRetrievalResult(
        EvidenceRetrievalMode.NEAREST,
        (EvidenceCandidate("redis_connection", "example-1", "evidence"),),
    )
    service = InvestigationService(
        collector=FakeCollector(CollectedEvidence(_raw_evidence(), ("traces",))),
        retrieve=lambda template: retrieval,
        find_knowledge=lambda key: None,
        generate=lambda prompt: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
        incident_store=FakeIncidentStore(),
    )

    result = service.investigate({"alert_name": "PodCrashLoop", "service": "orders"})

    assert result["diagnosis"]["diagnosis_cause"] is None
    assert result["diagnosis"]["hypothesis"] == "The observed structured error may explain the alert."
    assert result["missing_evidence"] == ["traces"]
