"""Small OpenAI-compatible adapter for grounded diagnosis generation."""

from __future__ import annotations

import json
from dataclasses import dataclass

import requests

from config import Settings


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMUnavailable(RuntimeError):
    """No configured provider returned a usable structured diagnosis."""


@dataclass(frozen=True)
class _Provider:
    url: str
    key: str
    model: str


class LLMClient:
    """Generate one JSON diagnosis with a bounded provider fallback."""

    def __init__(self, settings: Settings, *, session=requests):
        self._settings = settings
        self._session = session

    def generate(self, prompt: str) -> dict:
        """Prefer Groq, then try OpenRouter without exposing provider failures upstream."""
        for provider in self._providers():
            try:
                return self._generate_from(provider, prompt)
            except (requests.RequestException, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        raise LLMUnavailable("No configured provider produced a JSON diagnosis")

    def _providers(self) -> tuple[_Provider, ...]:
        providers = []
        if self._settings.groq_api_key:
            providers.append(_Provider(GROQ_URL, self._settings.groq_api_key, "llama-3.3-70b-versatile"))
        if self._settings.openrouter_api_key:
            providers.append(_Provider(
                OPENROUTER_URL,
                self._settings.openrouter_api_key,
                "meta-llama/llama-3.3-70b-instruct:free",
            ))
        return tuple(providers)

    def _generate_from(self, provider: _Provider, prompt: str) -> dict:
        response = self._session.post(
            provider.url,
            headers={"Authorization": f"Bearer {provider.key}", "Content-Type": "application/json"},
            json={
                "model": provider.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 400,
            },
            timeout=30,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"].get("content") or ""
        result = json.loads(_strip_code_fence(content))
        if not isinstance(result, dict):
            raise ValueError("provider response must be a JSON object")
        return result


def _strip_code_fence(content: str) -> str:
    """Accept a fenced JSON answer without accepting prose around it."""
    return content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
