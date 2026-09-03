from config import Settings


def test_settings_reads_service_urls(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("KUBECTL_EXECUTOR_URL", "http://executor:8080")

    settings = Settings.from_environment()

    assert settings.redis_url == "redis://redis:6379/0"
    assert settings.kubectl_executor_url == "http://executor:8080"


def test_settings_marks_missing_llm_keys_as_unavailable(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert Settings.from_environment().llm_configured is False
