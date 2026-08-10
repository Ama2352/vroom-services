import json, time, uuid
import redis as redis_lib
from retrieval.bm25 import PositiveIdfBM25, tokenize

INDEX_KEY = "incidents:index"

_BM25 = PositiveIdfBM25
_tokenize = tokenize


def connect(url: str) -> redis_lib.Redis:
    return redis_lib.from_url(url)


def _get_field(raw: dict, k: str) -> str:
    v = raw.get(k) or raw.get(k.encode() if isinstance(k, str) else k)
    return v.decode() if isinstance(v, bytes) else (v or "")


def build_symptom_text(alert_name: str, waiting_reason: str = "", log_error: str = "") -> str:
    return f"{alert_name} {waiting_reason} {log_error}".strip()


KNOWLEDGE_INDEX = "knowledge:index"
CORPUS_VERSION_KEY = "knowledge:corpus_version"


def get_corpus_version(rdb: redis_lib.Redis) -> int:
    raw = rdb.get(CORPUS_VERSION_KEY)
    if raw is None:
        return 0
    if isinstance(raw, bytes):
        raw = raw.decode()
    return int(raw)


def bump_corpus_version(rdb: redis_lib.Redis) -> int:
    return int(rdb.incr(CORPUS_VERSION_KEY))


def _hash_to_dict(raw: dict) -> dict:
    return {
        (k.decode() if isinstance(k, bytes) else k):
        (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw.items()
    }


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() == "true"


def store_knowledge_entry(rdb: redis_lib.Redis, entry: dict) -> str:
    key = entry["key"]
    rdb.hset(f"knowledge:entry:{key}", mapping={
        "key":                    key,
        "root_cause_pattern":     entry.get("root_cause_pattern", ""),
        "fix_action":             entry.get("fix_action", ""),
        "trigger_waiting_reason": entry.get("trigger_waiting_reason", ""),
        "conclusive":             "true" if entry.get("conclusive") else "false",
        "source":                 entry.get("source", "learned"),
        "created_by":             entry.get("created_by", ""),
        "last_modified_by":       entry.get("last_modified_by", ""),
        "last_modified_at":       entry.get("last_modified_at", ""),
    })
    rdb.sadd(KNOWLEDGE_INDEX, key)
    bump_corpus_version(rdb)
    return key


def get_knowledge_entry(rdb: redis_lib.Redis, key: str) -> dict | None:
    raw = rdb.hgetall(f"knowledge:entry:{key}")
    if not raw:
        return None
    d = _hash_to_dict(raw)
    d["conclusive"] = _to_bool(d.get("conclusive"))
    return d


def list_knowledge_entries(rdb: redis_lib.Redis) -> list:
    keys = rdb.smembers(KNOWLEDGE_INDEX)
    out = []
    for k in keys:
        k_str = k.decode() if isinstance(k, bytes) else k
        entry = get_knowledge_entry(rdb, k_str)
        if entry:
            out.append(entry)
    return out


def update_knowledge_entry(rdb: redis_lib.Redis, key: str, fields: dict) -> bool:
    if not rdb.exists(f"knowledge:entry:{key}"):
        return False
    mapping = {}
    if "root_cause_pattern" in fields:
        mapping["root_cause_pattern"] = fields["root_cause_pattern"]
    if "fix_action" in fields:
        mapping["fix_action"] = fields["fix_action"]
    if "trigger_waiting_reason" in fields:
        mapping["trigger_waiting_reason"] = fields["trigger_waiting_reason"]
    if "conclusive" in fields:
        mapping["conclusive"] = "true" if fields["conclusive"] else "false"
    if "last_modified_by" in fields:
        mapping["last_modified_by"] = fields["last_modified_by"]
        mapping["last_modified_at"] = str(int(time.time()))
    if mapping:
        rdb.hset(f"knowledge:entry:{key}", mapping=mapping)
        bump_corpus_version(rdb)
    return True


def delete_knowledge_entry(rdb: redis_lib.Redis, key: str) -> str:
    if not rdb.exists(f"knowledge:entry:{key}"):
        return "not_found"
    if list_history_entries_for_knowledge(rdb, key):
        return "has_history"
    rdb.delete(f"knowledge:entry:{key}")
    rdb.srem(KNOWLEDGE_INDEX, key)
    bump_corpus_version(rdb)
    return "deleted"


HISTORY_INDEX = "history:index"


def store_history_entry(rdb: redis_lib.Redis, entry: dict) -> str:
    hid = str(uuid.uuid4())
    rdb.hset(f"history:entry:{hid}", mapping={
        "service":           entry.get("service", ""),
        "knowledge_key":     entry["knowledge_key"],
        "symptom":           entry.get("symptom", ""),
        "context_notes":     entry.get("context_notes", ""),
        "source":            entry.get("source", "learned"),
        "timestamp":         str(int(time.time())),
        "created_by":        entry.get("created_by", ""),
        "last_modified_by":  entry.get("last_modified_by", ""),
        "last_modified_at":  entry.get("last_modified_at", ""),
    })
    rdb.sadd(HISTORY_INDEX, hid)
    bump_corpus_version(rdb)
    return hid


def get_history_entry(rdb: redis_lib.Redis, hid: str) -> dict | None:
    raw = rdb.hgetall(f"history:entry:{hid}")
    if not raw:
        return None
    d = _hash_to_dict(raw)
    d["id"] = hid
    return d


def list_history_entries_for_knowledge(rdb: redis_lib.Redis, knowledge_key: str) -> list:
    return [e for e in list_all_history_entries(rdb) if e.get("knowledge_key") == knowledge_key]


def list_all_history_entries(rdb: redis_lib.Redis) -> list:
    ids = rdb.smembers(HISTORY_INDEX)
    out = []
    for i in ids:
        i_str = i.decode() if isinstance(i, bytes) else i
        entry = get_history_entry(rdb, i_str)
        if entry:
            out.append(entry)
    return out


def update_history_entry(rdb: redis_lib.Redis, hid: str, fields: dict) -> bool:
    if not rdb.exists(f"history:entry:{hid}"):
        return False
    mapping = {}
    if "symptom" in fields:
        mapping["symptom"] = fields["symptom"]
    if "context_notes" in fields:
        mapping["context_notes"] = fields["context_notes"]
    if "knowledge_key" in fields:
        mapping["knowledge_key"] = fields["knowledge_key"]
    if "service" in fields:
        mapping["service"] = fields["service"]
    if "last_modified_by" in fields:
        mapping["last_modified_by"] = fields["last_modified_by"]
        mapping["last_modified_at"] = str(int(time.time()))
    if mapping:
        rdb.hset(f"history:entry:{hid}", mapping=mapping)
        bump_corpus_version(rdb)
    return True


def delete_history_entry(rdb: redis_lib.Redis, hid: str) -> bool:
    if not rdb.exists(f"history:entry:{hid}"):
        return False
    rdb.delete(f"history:entry:{hid}")
    rdb.srem(HISTORY_INDEX, hid)
    bump_corpus_version(rdb)
    return True


PENDING_INDEX = "pending:index"


def store_pending_suggestion(rdb: redis_lib.Redis, suggestion: dict) -> str:
    pid = str(uuid.uuid4())
    rdb.hset(f"pending:suggestion:{pid}", mapping={
        "service":                suggestion.get("service", ""),
        "symptom":                suggestion.get("symptom", ""),
        "proposed_knowledge_key": suggestion.get("proposed_knowledge_key", ""),
        "is_new_knowledge_key":   "true" if suggestion.get("is_new_knowledge_key") else "false",
        "root_cause":             suggestion.get("root_cause", ""),
        "fix_action":             suggestion.get("fix_action", ""),
        "context_notes":          suggestion.get("context_notes", ""),
        "source_incident_id":     suggestion.get("source_incident_id", ""),
        "trigger_waiting_reason": suggestion.get("trigger_waiting_reason", ""),
        "created_at":             str(int(time.time())),
        "status":                 "pending",
        "decided_by":             "",
        "decided_at":             "",
        "decision_reason":        "",
    })
    rdb.sadd(PENDING_INDEX, pid)
    return pid


def get_pending_suggestion(rdb: redis_lib.Redis, pid: str) -> dict | None:
    raw = rdb.hgetall(f"pending:suggestion:{pid}")
    if not raw:
        return None
    d = _hash_to_dict(raw)
    d["id"] = pid
    d["is_new_knowledge_key"] = _to_bool(d.get("is_new_knowledge_key"))
    return d


def list_pending_suggestions(rdb: redis_lib.Redis, status: str | None = None) -> list:
    ids = rdb.smembers(PENDING_INDEX)
    out = []
    for i in ids:
        i_str = i.decode() if isinstance(i, bytes) else i
        s = get_pending_suggestion(rdb, i_str)
        if s and (status is None or s.get("status") == status):
            out.append(s)
    return out


def approve_pending_suggestion(rdb: redis_lib.Redis, pending_id: str, actor: str, mode: str,
                                knowledge_key: str, symptom: str, context_notes: str,
                                root_cause_pattern: str | None = None,
                                fix_action: str | None = None,
                                conclusive: bool = False,
                                trigger_waiting_reason: str = "") -> str | None:
    suggestion = get_pending_suggestion(rdb, pending_id)
    if suggestion is None:
        return None

    if mode == "new":
        store_knowledge_entry(rdb, {
            "key":                    knowledge_key,
            "root_cause_pattern":     root_cause_pattern or "",
            "fix_action":             fix_action or "",
            "trigger_waiting_reason": trigger_waiting_reason,
            "conclusive":             conclusive,
            "source":                 "learned",
            "created_by":             actor,
        })

    hid = store_history_entry(rdb, {
        "service":       suggestion.get("service", ""),
        "knowledge_key": knowledge_key,
        "symptom":       symptom,
        "context_notes": context_notes,
        "source":        "learned",
        "created_by":    actor,
    })

    rdb.hset(f"pending:suggestion:{pending_id}", mapping={
        "status":     "approved",
        "decided_by": actor,
        "decided_at": str(int(time.time())),
    })
    return hid


def reject_pending_suggestion(rdb: redis_lib.Redis, pending_id: str, actor: str,
                               decision_reason: str | None = None) -> bool:
    if not rdb.exists(f"pending:suggestion:{pending_id}"):
        return False
    rdb.hset(f"pending:suggestion:{pending_id}", mapping={
        "status":          "rejected",
        "decided_by":      actor,
        "decided_at":      str(int(time.time())),
        "decision_reason": decision_reason or "",
    })
    return True


OPEN_INDEX = "incident:open_index"

_INCIDENT_EVIDENCE_FIELDS = [
    "pods_available", "pods_desired", "pods_running", "pods_ready", "waiting_reason", "last_terminated_reason",
    "restarts", "init_waiting_reason", "init_last_terminated_reason", "init_restarts",
    "log_error", "event_reason", "event_message", "event_object",
    "impact", "log_evidence", "trace_handoff", "diagnosis_confidence",
    "presentation", "retrieval_support",
]

_OCCURRENCE_SNAPSHOT_FIELDS = [
    "root_cause", "dev_action", "kubectl_hint", "low_confidence",
    "pods_available", "pods_desired", "pods_running", "pods_ready",
    "waiting_reason", "last_terminated_reason", "restarts",
    "init_waiting_reason", "init_last_terminated_reason", "init_restarts",
    "log_error", "event_reason", "event_message", "event_object",
    "impact", "log_evidence", "trace_handoff", "diagnosis_confidence",
    "configuration_diff", "dependency", "diagnosis_decision",
    "causal_chain_summary", "presentation", "retrieval_support",
]


def _evidence_snapshot(occurrence: dict) -> dict:
    return {f: occurrence.get(f, "") for f in _INCIDENT_EVIDENCE_FIELDS}


def _occurrence_snapshot(occurrence: dict) -> dict:
    return {f: occurrence.get(f, "") for f in _OCCURRENCE_SNAPSHOT_FIELDS}


def _serialize_evidence(value):
    return json.dumps(value) if isinstance(value, (dict, list)) else value


def append_incident_timeline(rdb: redis_lib.Redis, iid: str, entry: dict) -> None:
    rdb.rpush(f"incident:{iid}:timeline", json.dumps(entry))


def get_incident_timeline(rdb: redis_lib.Redis, iid: str) -> list:
    raw = rdb.lrange(f"incident:{iid}:timeline", 0, -1)
    return [json.loads(r.decode() if isinstance(r, bytes) else r) for r in raw]


def record_incident_occurrence(rdb: redis_lib.Redis, occurrence: dict) -> str:
    """U2: merge into a currently-open incident for the same (service, alert_name), or
    create a new one. Evidence fields always reflect the latest occurrence; older
    snapshots live only in the timeline."""
    for oid in rdb.smembers(OPEN_INDEX):
        oid_str  = oid.decode() if isinstance(oid, bytes) else oid
        existing = get_incident(rdb, oid_str)
        if (existing and existing.get("service") == occurrence["service"]
                and existing.get("alert_name") == occurrence["alert_name"]):
            mapping = {f: _serialize_evidence(occurrence.get(f, "")) for f in _INCIDENT_EVIDENCE_FIELDS}
            mapping.update({
                "root_cause":     occurrence.get("root_cause", ""),
                "dev_action":     occurrence.get("dev_action", ""),
                "kubectl_hint":   occurrence.get("kubectl_hint", ""),
                "low_confidence": "true" if occurrence.get("low_confidence") else "false",
                "configuration_diff": json.dumps(occurrence.get("configuration_diff")),
                "dependency":     json.dumps(occurrence.get("dependency")),
                "diagnosis_decision": json.dumps(occurrence.get("diagnosis_decision")),
                "causal_chain_summary": json.dumps(occurrence.get("causal_chain_summary")),
            })
            rdb.hset(f"incident:{oid_str}", mapping=mapping)
            append_incident_timeline(rdb, oid_str, {
                "type": "fired", "timestamp": int(time.time()),
                "occurrence_snapshot": _occurrence_snapshot(occurrence),
                "evidence_snapshot": _evidence_snapshot(occurrence),
            })
            return oid_str

    iid = str(uuid.uuid4())
    mapping = {
        "alert_name": occurrence["alert_name"],
        "service":    occurrence["service"],
        "namespace":  occurrence.get("namespace", ""),
        "timestamp":  str(int(time.time())),
        "root_cause":     occurrence.get("root_cause", ""),
        "dev_action":     occurrence.get("dev_action", ""),
        "kubectl_hint":   occurrence.get("kubectl_hint", ""),
        "low_confidence": "true" if occurrence.get("low_confidence") else "false",
        "configuration_diff": json.dumps(occurrence.get("configuration_diff")),
        "dependency":     json.dumps(occurrence.get("dependency")),
        "diagnosis_decision": json.dumps(occurrence.get("diagnosis_decision")),
        "causal_chain_summary": json.dumps(occurrence.get("causal_chain_summary")),
        "presentation": json.dumps(occurrence.get("presentation")),
        "retrieval_support": json.dumps(occurrence.get("retrieval_support")),
        "status":       "open",
        "resolved_at":  "",
        "resolved_by":  "",
    }
    mapping.update({f: _serialize_evidence(occurrence.get(f, "")) for f in _INCIDENT_EVIDENCE_FIELDS})
    rdb.hset(f"incident:{iid}", mapping=mapping)
    rdb.sadd(INDEX_KEY, iid)
    rdb.sadd(OPEN_INDEX, iid)
    append_incident_timeline(rdb, iid, {
        "type": "fired", "timestamp": int(time.time()),
        "occurrence_snapshot": _occurrence_snapshot(occurrence),
        "evidence_snapshot": _evidence_snapshot(occurrence),
    })
    return iid


def get_incident(rdb: redis_lib.Redis, iid: str) -> dict | None:
    raw = rdb.hgetall(f"incident:{iid}")
    if not raw:
        return None
    d = _hash_to_dict(raw)
    d["id"] = iid
    d["low_confidence"] = _to_bool(d.get("low_confidence"))
    for f in ("pods_available", "pods_desired", "pods_running", "pods_ready", "restarts", "init_restarts"):
        d[f] = int(d.get(f) or 0)
    for f in ("impact", "log_evidence", "trace_handoff", "diagnosis_confidence", "presentation", "retrieval_support"):
        if isinstance(d.get(f), str) and d[f]:
            try:
                d[f] = json.loads(d[f])
            except json.JSONDecodeError:
                pass
    d["configuration_diff"] = json.loads(d["configuration_diff"]) if "configuration_diff" in d else None
    d["dependency"] = json.loads(d["dependency"]) if "dependency" in d else None
    d["diagnosis_decision"] = json.loads(d["diagnosis_decision"]) if "diagnosis_decision" in d else None
    d["causal_chain_summary"] = json.loads(d["causal_chain_summary"]) if "causal_chain_summary" in d else None
    return d


def _decode_occurrence_snapshot(snapshot: dict) -> dict:
    result = dict(snapshot)
    for field in ("low_confidence",):
        if field in result:
            result[field] = _to_bool(result[field])
    for field in ("pods_available", "pods_desired", "pods_running", "pods_ready", "restarts",
                  "init_restarts"):
        if field in result:
            try:
                result[field] = int(result[field] or 0)
            except (TypeError, ValueError):
                result[field] = 0
    for field in ("impact", "log_evidence", "trace_handoff", "diagnosis_confidence",
                  "configuration_diff", "dependency", "diagnosis_decision",
                  "causal_chain_summary", "presentation", "retrieval_support"):
        if isinstance(result.get(field), str) and result[field]:
            try:
                result[field] = json.loads(result[field])
            except json.JSONDecodeError:
                pass
    return result


def get_incident_occurrences(rdb: redis_lib.Redis, iid: str) -> list[dict]:
    """Return immutable fired-to-fired views with their own steps and presentation."""
    occurrences: list[dict] = []
    current: dict | None = None
    for entry in get_incident_timeline(rdb, iid):
        if entry.get("type") == "fired":
            if current is not None:
                occurrences.append(current)
            snapshot = entry.get("occurrence_snapshot") or entry.get("evidence_snapshot") or {}
            snapshot = _decode_occurrence_snapshot(snapshot)
            if "presentation" not in snapshot or not snapshot.get("presentation"):
                snapshot["presentation"] = {
                    "verdict": "evaluation_unavailable",
                    "headline": snapshot.get("root_cause") or snapshot.get("log_error") or "Legacy incident",
                    "summary": "Evaluation data was not stored for this occurrence.",
                    "confirmed_failure": snapshot.get("log_error") or "Legacy incident evidence",
                    "causal_basis": None,
                    "evidence_gap": "Evaluation data unavailable",
                    "evidence_confidence": "unknown",
                    "answer_source": "safe_fallback",
                    "supporting_evidence": [],
                    "recommended_response": {"mode": "investigation", "summary": "Review the stored incident evidence.", "command": None},
                    "incident_events": [],
                }
            current = {
                "index": len(occurrences),
                "fired_at": int(entry.get("timestamp") or 0),
                **snapshot,
                "evidence": {
                    key: snapshot.get(key)
                    for key in (
                        "impact", "log_evidence", "trace_handoff", "pods_available",
                        "pods_desired", "pods_running", "pods_ready", "waiting_reason",
                        "restarts", "configuration_diff", "dependency",
                    )
                    if key in snapshot
                },
                "agent_steps": [],
            }
        elif current is not None:
            if entry.get("type") == "step":
                current["agent_steps"].append(entry)
            elif entry.get("type") == "resolved":
                current.setdefault("lifecycle_events", []).append(entry)
    if current is not None:
        occurrences.append(current)
    return occurrences


def list_incidents(rdb: redis_lib.Redis, status: str | None = None) -> list:
    out = []
    for i in rdb.smembers(INDEX_KEY):
        i_str = i.decode() if isinstance(i, bytes) else i
        entry = get_incident(rdb, i_str)
        if entry and (status is None or entry.get("status") == status):
            out.append(entry)
    return out


def get_latest_incident(rdb: redis_lib.Redis) -> dict | None:
    incidents = list_incidents(rdb)
    if not incidents:
        return None

    def _last_activity(inc: dict) -> tuple:
        timeline   = get_incident_timeline(rdb, inc["id"])
        timestamps = [int(inc.get("timestamp") or 0)] + [e.get("timestamp", 0) for e in timeline]
        # Timeline length is a tiebreaker for same-second timestamps (int(time.time())
        # precision) — an incident with more entries has had more recent activity even
        # when two occurrences land in the same wall-clock second.
        return (max(timestamps), len(timeline))

    return max(incidents, key=_last_activity)


def resolve_incident(rdb: redis_lib.Redis, iid: str, actor: str) -> bool:
    if not rdb.exists(f"incident:{iid}"):
        return False
    rdb.hset(f"incident:{iid}", mapping={
        "status":      "resolved",
        "resolved_at": str(int(time.time())),
        "resolved_by": actor,
    })
    rdb.srem(OPEN_INDEX, iid)
    append_incident_timeline(rdb, iid, {
        "type": "resolved", "timestamp": int(time.time()), "actor": actor,
    })
    return True


def _score_all(rdb: redis_lib.Redis, query: str) -> list:
    keys = rdb.smembers(INDEX_KEY)
    if not keys:
        return []

    items, corpus_tokens = [], []
    for key in keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        raw = rdb.hgetall(f"incident:{key_str}")
        if not raw:
            continue
        text = build_symptom_text(
            _get_field(raw, "alert_name"),
            _get_field(raw, "waiting_reason"),
            _get_field(raw, "log_error"),
        )
        corpus_tokens.append(_tokenize(text))
        items.append({
            "alert_name":     _get_field(raw, "alert_name"),
            "service":        _get_field(raw, "service"),
            "waiting_reason": _get_field(raw, "waiting_reason"),
            "root_cause":     _get_field(raw, "root_cause"),
            "kubectl_hint":   _get_field(raw, "kubectl_hint"),
            "timestamp":      int(_get_field(raw, "timestamp") or 0),
        })

    if not corpus_tokens:
        return []

    bm25       = _BM25(corpus_tokens)
    raw_scores = bm25.get_scores(_tokenize(query))
    max_score  = max(raw_scores)          # corpus_tokens non-empty here, so raw_scores is too
    if max_score <= 0:
        return []

    scored = [(s / max_score, item) for s, item in zip(raw_scores, items) if s > 0]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _diversify(scored: list, top_k: int) -> list:
    seen, picked = set(), []
    for score, item in scored:
        key = (item["service"], item["alert_name"], item["waiting_reason"])
        if key in seen:
            continue
        seen.add(key)
        picked.append((score, item))
        if len(picked) >= top_k:
            break
    return picked


def search_memory_items(rdb: redis_lib.Redis, query: str, limit: int = 3) -> list:
    scored = _score_all(rdb, query)
    if not scored:
        return []
    diverse = _diversify(scored, limit)
    return [{**item, "score": score} for score, item in diverse]


def format_incidents(items: list) -> str:
    lines = []
    for i, item in enumerate(items, 1):
        lines.append(
            f"[{i}] (similarity: {item['score']:.2f}) {item['alert_name']} on {item['service']} → "
            f"root cause: {item['root_cause']} → "
            f"{item.get('kubectl_hint') or 'no action'}"
        )
    return "\n".join(lines)


def search_memory(rdb: redis_lib.Redis, query: str, limit: int = 3) -> str:
    items = search_memory_items(rdb, query, limit)
    if not items:
        return "no relevant memory found"
    return format_incidents(items)


