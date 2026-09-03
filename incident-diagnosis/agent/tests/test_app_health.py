from app import create_app
from config import Settings


def test_health_reports_configured_dependencies():
    settings = Settings(
        redis_url="redis://redis:6379/0",
        prometheus_url="",
        loki_url="",
        tempo_url="",
        kubectl_executor_url="",
        kubectl_executor_api_key="",
        groq_api_key="configured-key",
        openrouter_api_key="",
    )

    response = create_app(settings).test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "dependencies": {"redis": "configured", "llm": "configured"},
    }
