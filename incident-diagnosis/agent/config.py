"""Environment settings for the incident-agent process."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Keep deployment choices outside request and diagnosis code."""

    redis_url: str
    prometheus_url: str
    loki_url: str
    tempo_url: str
    kubectl_executor_url: str
    kubectl_executor_api_key: str
    groq_api_key: str
    openrouter_api_key: str

    @property
    def llm_configured(self) -> bool:
        """A missing provider key must lead to a safe degraded diagnosis later."""
        return bool(self.groq_api_key or self.openrouter_api_key)

    @classmethod
    def from_environment(cls) -> "Settings":
        """Read settings once when the application is constructed."""
        return cls(
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            prometheus_url=os.getenv("PROMETHEUS_URL", ""),
            loki_url=os.getenv("LOKI_URL", ""),
            tempo_url=os.getenv("TEMPO_URL", ""),
            kubectl_executor_url=os.getenv("KUBECTL_EXECUTOR_URL", ""),
            kubectl_executor_api_key=os.getenv("EXECUTOR_API_KEY", ""),
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        )
