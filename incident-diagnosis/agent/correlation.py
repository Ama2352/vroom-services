import json
import os
import re
from urllib.parse import urlencode

import requests

LOKI_URL = os.environ.get("LOKI_URL", "http://loki-stack.monitoring.svc.cluster.local:3100/loki/api/v1/query_range")
TEMPO_URL = os.environ.get("TEMPO_URL", "http://tempo.monitoring.svc.cluster.local:3100")
GRAFANA_BASE_URL = os.environ.get("GRAFANA_BASE_URL", "http://localhost/grafana")
TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
PRE_ALERT_LOOKBACK_SECONDS = 120


def _error(status, **fields):
    return {"status": status, **fields}


def derive_log_error(log_evidence: dict) -> str:
    """Return the displayed error from the same canonical Loki record."""
    return log_evidence.get("message", "") if log_evidence.get("status") == "found" else ""


def collect_log_evidence(service, namespace, start_epoch_s, end_epoch_s):
    query_start_epoch_s = start_epoch_s - PRE_ALERT_LOOKBACK_SECONDS
    params = {
        "start": str(int(query_start_epoch_s * 1_000_000_000)),
        "end": str(int(end_epoch_s * 1_000_000_000)), "limit": "50",
    }

    def query_candidates(query, *, plain=False):
        response = requests.get(LOKI_URL, params={"query": query, **params}, timeout=5)
        if not response.ok:
            return None, _error("unavailable", errors=[f"Loki HTTP {response.status_code}"])
        candidates = []
        for stream in response.json().get("data", {}).get("result", []):
            stream_pod = stream.get("stream", {}).get("pod", "")
            for timestamp, raw_line in stream.get("values", []):
                record = None
                try:
                    record = json.loads(raw_line)
                except (TypeError, json.JSONDecodeError):
                    if not plain:
                        continue
                if plain:
                    message = raw_line if record is None else record.get("message") or record.get("msg", "")
                    if not message:
                        continue
                    candidates.append((abs(int(timestamp) / 1_000_000_000 - start_epoch_s), int(timestamp), {
                        "message": message, "trace_id": "", "pod": stream_pod,
                        "log_format": "plain",
                    }))
                    continue
                trace_id = record.get("trace_id", "")
                if (str(record.get("level", "")).lower() != "error" or record.get("service", service) != service
                        or not TRACE_ID_RE.fullmatch(trace_id)):
                    continue
                candidates.append((abs(int(timestamp) / 1_000_000_000 - start_epoch_s), int(timestamp), {
                    **record, "pod": stream_pod, "log_format": "structured",
                }))
        return candidates, None

    try:
        structured_query = f'{{app="{service}",namespace="{namespace}"}} | json | level=~"(?i)^error$"'
        candidates, error = query_candidates(structured_query)
        if error:
            return error
        if not candidates:
            plain_query = f'{{app="{service}",namespace="{namespace}"}} |~ "(?i)(error|failed|not ready|panic|fatal|refused|lookup)"'
            candidates, error = query_candidates(plain_query, plain=True)
            if error:
                return error
        if not candidates:
            return _error("no_match")
        _, timestamp_ns, record = min(candidates, key=lambda item: item[0])
        return {"status": "found", "service": service, "namespace": namespace,
                "trace_id": record.get("trace_id", ""), "span_id": record.get("span_id", ""),
                "operation": record.get("operation", ""),
                "message": record.get("message") or record.get("msg", ""),
                "event_id": record.get("event_id", ""), "pod": record.get("pod", ""),
                "log_format": record.get("log_format", "structured"),
                "timestamp": record.get("timestamp") or record.get("time", ""),
                "timestamp_ns": timestamp_ns}
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


def correlate_trace(log_evidence, start_epoch_s=None, end_epoch_s=None):
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
    if start_epoch_s is not None and end_epoch_s is not None:
        try:
            trace_start = int(selected.get("startTimeUnixNano", 0)) / 1_000_000_000
            trace_end = int(selected.get("endTimeUnixNano", selected.get("startTimeUnixNano", 0))) / 1_000_000_000
            if trace_start and trace_end and (trace_end < start_epoch_s or trace_start > end_epoch_s):
                return _error("conflict", trace_id=trace_id, reason="trace outside incident window")
        except (TypeError, ValueError):
            return _error("conflict", trace_id=trace_id, reason="trace outside incident window")
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

    involved_services = []
    ordered_spans = sorted(
        enumerate(spans),
        key=lambda item: (
            int(item[1].get("startTimeUnixNano", 0)) if str(item[1].get("startTimeUnixNano", "")).isdigit()
            and int(item[1].get("startTimeUnixNano", 0)) > 0 else 2**63,
            item[0],
        ),
    )
    for _, span in ordered_spans:
        service_name = span.get("service_name", "")
        if service_name and service_name not in involved_services:
            involved_services.append(service_name)
    return {"status": "correlated", "trace_id": trace_id, "error_service": selected.get("service_name", ""),
            "error_operation": operation, "error_message": message,
            "involved_services": involved_services,
            "grafana_url": f"{GRAFANA_BASE_URL.rstrip('/')}/explore?{urlencode({'left': json.dumps({'datasource': 'Tempo', 'queries': [{'queryType': 'traceql', 'query': trace_id}]})})}"}
