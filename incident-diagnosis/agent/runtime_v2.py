"""Pure v2 incident occurrence builders.

Collection owns observations.  This module only gives those observations a stable
API/UI shape; it makes no causal decision and never consults retrieved knowledge.
"""

from __future__ import annotations

from typing import Any


def _count(value: Any, noun: str) -> str:
    number = int(value or 0)
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _metric_rows(impact: dict[str, Any], operational: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    operational = operational or {}
    source = [
        ("request rate", impact.get("request_rate"), "req/s"),
        ("HTTP error rate", impact.get("error_rate_percent"), "%"),
        ("p95 latency", impact.get("p95_latency_ms"), "ms"),
    ]
    rows = [{"field": name, "value": value, "unit": unit, "status": "available" if value is not None else "no_data"}
            for name, value, unit in source]
    for name, key in (("CPU usage", "cpu_usage"), ("memory working set", "memory_working_set"),
                      ("ephemeral storage", "ephemeral_storage"), ("CPU throttling", "cpu_throttling")):
        item = operational.get(key) or {}
        rows.append({"field": name, "value": item.get("value"), "unit": item.get("unit", ""),
                     "status": item.get("status", "no_data")})
    return rows


def build_raw_evidence(template, facts: dict[str, Any], impact: dict[str, Any], log: dict[str, Any],
                       trace: dict[str, Any], configuration: dict[str, Any],
                       operational: dict[str, Any] | None = None) -> dict[str, Any]:
    cards: dict[str, Any] = {
        "metrics": {"state": "confirmed", "title": "Alert and metrics", "rows": _metric_rows(impact, operational)},
        "kubernetes": {"state": "context", "title": "Kubernetes", "rows": [
            {"field": "ready", "value": _count(facts.get("pods_ready"), "pod")},
            {"field": "desired", "value": _count(facts.get("pods_desired"), "pod")},
            {"field": "restarts", "value": _count(facts.get("restarts"), "pod restart")},
        ]},
    }
    if log.get("status") == "found" and log.get("message"):
        cards["logs"] = {"state": "confirmed", "title": "Structured log", "rows": [
            {"field": "log_error", "value": log["message"]},
        ]}
    if trace.get("status") == "correlated":
        cards["traces"] = {"state": "confirmed", "title": "Correlated trace", "rows": [
            {"field": "services", "value": " → ".join(trace.get("service_path") or trace.get("involved_services") or [])},
            {"field": "operation", "value": trace.get("error_operation", "")},
            {"field": "error", "value": trace.get("error_message", "")},
        ], "href": trace.get("grafana_url")}
    changes = configuration.get("changes") if isinstance(configuration, dict) else []
    if configuration.get("status") == "changed" and changes:
        rows = []
        for change in changes:
            rows.extend([
                {"field": "field", "value": change.get("path", "")},
                {"field": "previous", "value": str(change.get("previous", "unset"))},
                {"field": "current", "value": str(change.get("current", "unset"))},
            ])
        cards["configuration"] = {"state": "confirmed", "title": "Configuration diff", "rows": rows}
    else:
        cards["configuration"] = {"state": "not_found", "title": "Configuration diff", "rows": [
            {"field": "status", "value": "No relevant workload change collected"},
        ]}
    return cards


def build_v2_occurrence(template, raw_evidence: dict[str, Any], diagnosis: dict[str, Any], retrieval: dict[str, Any]) -> dict[str, Any]:
    return {
        "diagnosis": {
            "evidence_analysis": diagnosis.get("evidence_analysis") or {},
            "incident_summary": diagnosis.get("incident_summary", ""),
            "diagnosis_cause": diagnosis.get("diagnosis_cause"),
            "hypothesis": diagnosis.get("hypothesis"),
            "recommended_action": diagnosis.get("recommended_action") or {},
            "used_knowledge_keys": diagnosis.get("used_knowledge_keys") or [],
            "evidence_refs": diagnosis.get("evidence_refs") or [],
            "hypothesis_evidence_refs": diagnosis.get("hypothesis_evidence_refs") or [],
        },
        "raw_evidence": raw_evidence,
        "retrieval": retrieval,
        "evidence_template": template.serialize(),
        "evidence_fingerprint": template.fingerprint(),
    }
