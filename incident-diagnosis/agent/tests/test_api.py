from app import create_app
from config import Settings


class FakeInvestigationService:
    def __init__(self):
        self.alerts = []

    def investigate(self, alert):
        self.alerts.append(alert)
        return {"id": "incident-1", "diagnosis": {"diagnosis_cause": None}}


def _settings():
    return Settings("redis://x/0", "", "", "", "", "", "", "")


def test_investigate_passes_the_alert_to_the_service():
    service = FakeInvestigationService()
    client = create_app(_settings(), investigation_service=service).test_client()

    response = client.post("/investigate", json={"service": "orders", "alert_name": "PodCrashLoop"})

    assert response.status_code == 200
    assert response.get_json()["id"] == "incident-1"
    assert service.alerts == [{"service": "orders", "alert_name": "PodCrashLoop"}]


def test_investigate_rejects_an_alert_without_a_service():
    client = create_app(_settings(), investigation_service=FakeInvestigationService()).test_client()

    response = client.post("/investigate", json={"alert_name": "PodCrashLoop"})

    assert response.status_code == 400
    assert response.get_json() == {"error": "service is required"}


class FakeIncidentStore:
    def list(self): return [{"id": "incident-1"}]
    def get(self, incident_id): return {"id": incident_id} if incident_id == "incident-1" else None


def test_incident_routes_read_from_the_store():
    client = create_app(_settings(), investigation_service=FakeInvestigationService(), incident_store=FakeIncidentStore()).test_client()
    assert client.get("/incidents").get_json() == [{"id": "incident-1"}]
    assert client.get("/incidents/incident-1").status_code == 200
    assert client.get("/incidents/missing").status_code == 404


class FakeKnowledgeStore:
    def __init__(self): self.value = {"families": [], "examples": [], "hints": []}
    def load(self): return self.value
    def save(self, value): self.value = value


def test_knowledge_routes_return_and_replace_the_reviewable_document():
    store = FakeKnowledgeStore()
    client = create_app(_settings(), investigation_service=FakeInvestigationService(), knowledge_store=store).test_client()
    assert client.get("/knowledge").get_json() == store.value
    body = {"families": [{"knowledge_key": "redis"}], "examples": [], "hints": []}
    assert client.put("/knowledge", json=body).status_code == 200
    assert store.value == body
