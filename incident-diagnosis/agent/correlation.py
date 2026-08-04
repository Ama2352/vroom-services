import json
import os
import re
from urllib.parse import urlencode

import requests

LOKI_URL = os.environ.get("LOKI_URL", "http://loki-stack.monitoring.svc.cluster.local:3100/loki/api/v1/query_range")
TEMPO_URL = os.environ.get("TEMPO_URL", "http://tempo.monitoring.svc.cluster.local:3100")
GRAFANA_BASE_URL = os.environ.get("GRAFANA_BASE_URL", "http://localhost/grafana")
TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _error(status, **fields):
    return {"status": status, **fields}


def collect_log_evidence(service, namespace, start_epoch_s, end_epoch_s):
    query = f'{{app="{service}",namespace="{namespace}"}} | json | level="error"'
    try:
        response = requests.get(LOKI_URL, params={
            "query": query, "start": str(int(start_epoch_s * 1_000_000_000)),
            "end": str(int(end_epoch_s * 1_000_000_000)), "limit": "50",
        }, timeout=5)
        if not response.ok:
            return _error("unavailable", errors=[f"Loki HTTP {response.status_code}"])
        candidates = []
        for stream in response.json().get("data", {}).get("result", []):
            for timestamp, raw_line in stream.get("values", []):
                try:
                    record = json.loads(raw_line)
                except (TypeError, json.JSONDecodeError):
                    continue
                trace_id = record.get("trace_id", "")
                if (record.get("level") != "error" or record.get("service", service) != service
                        or not TRACE_ID_RE.fullmatch(trace_id)):
                    continue
                candidates.append((abs(int(timestamp) / 1_000_000_000 - start_epoch_s), record))
        if not candidates:
            return _error("no_match")
        record = min(candidates, key=lambda item: item[0])[1]
        return {"status": "found", "service": service, "namespace": namespace,
                "trace_id": record["trace_id"], "span_id": record.get("span_id", ""),
                "operation": record.get("operation", ""), "message": record.get("message", ""),
                "event_id": record.get("event_id", ""), "timestamp": record.get("timestamp", "")}
    except Exception as exc:
        return _error("unavailable", errors=[str(exc)])


def fetch_trace(trace_id):
    if not TRACE_ID_RE.fullmatch(trace_id or ""):
        return _error("not_found")
    try:
        response = requests.get(f"{TEMPO_URL}/api/traces/{trace_id}", timeout=5)
        if response.status_code == 404:
            return _error("not_found", trace_id=trace_id)
        if not response.ok:
            return _error("unavailable", trace_id=trace_id)
        return {"status": "fetched", "trace_id": trace_id, "payload": response.json()}
    except Exception as exc:
        return _error("unavailable", trace_id=trace_id, errors=[str(exc)])


def correlate_trace(log_evidence):
    trace_id = log_evidence.get("trace_id")
    if not trace_id:
        return _error("no_trace_id")
    fetched = fetch_trace(trace_id)
    if fetched["status"] != "fetched":
        return fetched
    spans = []
    for batch in fetched["payload"].get("batches", []):
        service_name = next((a.get("value", {}).get("stringValue", "") for a in batch.get("resource", {}).get("attributes", []) if a.get("key") == "service.name"), "")
        for scope in batch.get("scopeSpans", []):
            for span in scope.get("spans", []):
                spans.append({**span, "service_name": service_name})
    failed = [span for span in spans if span.get("status", {}).get("code") == "STATUS_CODE_ERROR" or any(event.get("name") == "exception" for event in span.get("events", []))]
    if not failed:
        return _error("conflict", trace_id=trace_id, reason="trace contains no error span")
    selected = failed[-1]
    operation = selected.get("name", "")
    message = ""
    for event in selected.get("events", []):
        for attr in event.get("attributes", []):
            if attr.get("key") in ("exception.message", "message"):
                message = attr.get("value", {}).get("stringValue", "")
    tokens = {token.lower() for token in re.findall(r"[A-Za-z0-9_/-]{5,}", f"{message} {operation}")}
    log_tokens = {token.lower() for token in re.findall(r"[A-Za-z0-9_/-]{5,}", f"{log_evidence.get('message', '')} {log_evidence.get('operation', '')}")}
    agrees = selected.get("service_name") == log_evidence.get("service") or operation == log_evidence.get("operation") or bool(tokens & log_tokens)
    if not agrees:
        return _error("conflict", trace_id=trace_id, error_service=selected.get("service_name", ""), error_operation=operation)
    return {"status": "correlated", "trace_id": trace_id, "error_service": selected.get("service_name", ""),
            "error_operation": operation, "error_message": message,
            "grafana_url": f"{GRAFANA_BASE_URL.rstrip('/')}/explore?{urlencode({'left': json.dumps({'datasource': 'Tempo', 'queries': [{'queryType': 'traceql', 'query': f'{{.trace:id={trace_id}}}'}]})})}"}
