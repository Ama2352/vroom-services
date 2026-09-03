import requests
import pytest

from config import Settings
from llm import LLMClient, LLMUnavailable


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self.content}}]}


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, *, headers, json, timeout):
        self.calls.append((url, headers, json, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_llm_client_uses_groq_first_and_parses_a_json_object():
    session = FakeSession([FakeResponse('{"incident_summary":"observed"}')])
    settings = Settings("redis://x/0", "", "", "", "", "", "groq-key", "router-key")

    result = LLMClient(settings, session=session).generate("diagnose this")

    assert result == {"incident_summary": "observed"}
    assert session.calls[0][0] == "https://api.groq.com/openai/v1/chat/completions"
    assert session.calls[0][2]["messages"] == [{"role": "user", "content": "diagnose this"}]


def test_llm_client_falls_back_to_openrouter_after_groq_failure():
    session = FakeSession([
        requests.RequestException("Groq unavailable"),
        FakeResponse('{"incident_summary":"observed"}'),
    ])
    settings = Settings("redis://x/0", "", "", "", "", "", "groq-key", "router-key")

    result = LLMClient(settings, session=session).generate("diagnose this")

    assert result["incident_summary"] == "observed"
    assert [call[0] for call in session.calls] == [
        "https://api.groq.com/openai/v1/chat/completions",
        "https://openrouter.ai/api/v1/chat/completions",
    ]


def test_llm_client_reports_unavailable_when_no_provider_is_configured():
    settings = Settings("redis://x/0", "", "", "", "", "", "", "")

    with pytest.raises(LLMUnavailable):
        LLMClient(settings).generate("diagnose this")
