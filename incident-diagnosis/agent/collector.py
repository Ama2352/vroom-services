"""Scoped collection of raw operational observations."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import requests

from config import Settings


class ObservationClient(Protocol):
    """A replaceable source of observations for one service scope."""

    def collect(self, service: str, namespace: str) -> dict[str, object]:
        """Return named observations; unavailable sources use ``None``."""


@dataclass(frozen=True)
class CollectedEvidence:
    """Raw facts plus the sources that could not contribute evidence."""

    raw: dict[str, object]
    missing: tuple[str, ...]


class EvidenceCollector:
    """Collect facts without deciding what they mean."""

    def __init__(self, client: ObservationClient):
        self._client = client

    def collect(self, alert: dict) -> CollectedEvidence:
        """Request observations for exactly the service and namespace in the alert."""
        service = str(alert.get("service", ""))
        namespace = str(alert.get("namespace", ""))
        raw = self._client.collect(service, namespace)
        missing = tuple(name for name, value in raw.items() if value is None)

        # An unavailable source reduces confidence; it must not erase other facts.
        return CollectedEvidence(raw=raw, missing=missing)


class HttpObservationClient:
    """Translate scoped observability responses into small evidence-friendly facts."""

    def __init__(self, settings: Settings, *, session=requests, now_seconds=None):
        self._settings = settings
        self._session = session
        self._now_seconds = now_seconds

    def collect(self, service: str, namespace: str) -> dict[str, object]:
        """Collect every source independently so one outage cannot hide the others."""
        errors: dict[str, str] = {}
        observations = {
            "metrics": self._collect_source("metrics", self._metrics, service, namespace, errors),
            "logs": self._collect_source("logs", self._logs, service, namespace, errors),
            "traces": self._collect_source("traces", self._traces, service, namespace, errors),
            "kubernetes": self._collect_source("kubernetes", self._kubernetes, service, namespace, errors),
            "configuration": self._collect_source("configuration", self._configuration, service, namespace, errors),
        }
        if errors:
            observations["collection_errors"] = errors
        return observations

    def _collect_source(self, name, function, service, namespace, errors):
        try:
            return function(service, namespace)
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            errors[name] = type(exc).__name__
            return None

    def _metrics(self, service: str, namespace: str) -> dict[str, str]:
        return {
            "waiting_reason": self._active_reason(
                "kube_pod_container_status_waiting_reason", service, namespace,
            ),
            "last_terminated_reason": self._active_reason(
                "kube_pod_container_status_last_terminated_reason", service, namespace,
            ),
        }

    def _active_reason(self, metric: str, service: str, namespace: str) -> str:
        query = f'{metric}{{namespace="{namespace}",pod=~"{service}-.*"}}'
        payload = self._get_json(self._settings.prometheus_url, {"query": query})
        for result in payload.get("data", {}).get("result", []):
            if result.get("value", [None, "0"])[1] == "1":
                return str(result.get("metric", {}).get("reason", ""))
        return ""

    def _logs(self, service: str, namespace: str) -> dict[str, str]:
        now = int((self._now_seconds() if self._now_seconds else time.time()) * 1_000_000_000)
        payload = self._get_json(
            self._settings.loki_url,
            {
                "query": f'{{app="{service}",namespace="{namespace}"}} |~ "(?i)(error|failed|panic|fatal|refused)"',
                "start": str(now - 15 * 60 * 1_000_000_000),
                "end": str(now),
                "limit": "1",
                "direction": "backward",
            },
        )
        results = payload.get("data", {}).get("result", [])
        values = results[0].get("values", []) if results else []
        return {"message": str(values[0][1])[:200] if values else ""}

    def _traces(self, service: str, namespace: str) -> dict[str, str]:
        payload = self._get_json(
            f"{self._settings.tempo_url.rstrip('/')}/api/search",
            {"tags": f"service.name={service}&error=true", "namespace": namespace},
        )
        traces = payload.get("traces", [])
        trace = traces[0] if traces else {}
        return {
            "error_service": str(trace.get("rootServiceName") or trace.get("serviceName") or ""),
            "error_operation": str(trace.get("rootTraceName") or trace.get("name") or ""),
            "error_message": str(trace.get("error") or ""),
        }

    def _kubernetes(self, service: str, namespace: str) -> dict[str, str]:
        payload = self._get_json(
            f"{self._settings.kubectl_executor_url.rstrip('/')}/tools/events-json",
            {"service": service, "namespace": namespace},
            headers=self._executor_headers(),
        )
        events = payload.get("events", [])
        event = events[-1] if events else {}
        return {
            "event_reason": str(event.get("reason", "")),
            "event_message": str(event.get("message", "")),
        }

    def _configuration(self, service: str, namespace: str) -> dict[str, object]:
        payload = self._get_json(
            f"{self._settings.kubectl_executor_url.rstrip('/')}/tools/workload-revisions",
            {"service": service, "namespace": namespace},
            headers=self._executor_headers(),
        )
        current, previous = payload.get("current") or {}, payload.get("previous") or {}
        changes = []
        for container in sorted(set(current) | set(previous)):
            for section in ("env", "resources"):
                before = (previous.get(container) or {}).get(section) or {}
                after = (current.get(container) or {}).get(section) or {}
                for key in sorted(set(before) | set(after)):
                    if before.get(key) != after.get(key):
                        changes.append({
                            "path": f"containers.{container}.{section}.{key}",
                            "previous": before.get(key),
                            "current": after.get(key),
                        })
        return {"status": "changed" if changes else "unchanged", "changes": changes}

    def _get_json(self, url: str, params: dict[str, str], *, headers=None) -> dict:
        if not url:
            raise requests.RequestException("service URL is not configured")
        response = self._session.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def _executor_headers(self) -> dict[str, str] | None:
        if not self._settings.kubectl_executor_api_key:
            return None
        return {"Authorization": f"Bearer {self._settings.kubectl_executor_api_key}"}
