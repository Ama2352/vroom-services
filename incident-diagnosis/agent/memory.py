import json, time, uuid, re, math
import redis as redis_lib
from rank_bm25 import BM25Okapi

INDEX_KEY = "incidents:index"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class _BM25(BM25Okapi):
    """BM25Okapi with Lucene/ATIRE-style idf smoothing (log(1 + ...)) instead of the
    library's default epsilon-floor on negative idf. The default formula can produce
    zero or negative idf for any term appearing in half or more of the corpus's
    documents — a routine occurrence for this project's small incident/runbook stores
    (e.g. a single stored incident, or two incidents sharing one alert_name token),
    which silently zeroes out or inverts genuine relevance signal instead of scoring
    an exact match highly. This smoothing keeps idf strictly positive for any term
    with 1 <= freq <= corpus_size, while a term entirely absent from every document
    is simply missing from `idf` (get(...) defaults to 0 contribution), preserving
    the "zero shared tokens -> zero score" floor this module's callers rely on."""

    def _calc_idf(self, nd):
        for word, freq in nd.items():
            self.idf[word] = math.log(1 + (self.corpus_size - freq + 0.5) / (freq + 0.5))


def _tokenize(text: str) -> list:
    return _TOKEN_RE.findall(text.lower())


def connect(url: str) -> redis_lib.Redis:
    return redis_lib.from_url(url)


def _get_field(raw: dict, k: str) -> str:
    v = raw.get(k) or raw.get(k.encode() if isinstance(k, str) else k)
    return v.decode() if isinstance(v, bytes) else (v or "")


def build_symptom_text(alert_name: str, waiting_reason: str = "", log_error: str = "") -> str:
    return f"{alert_name} {waiting_reason} {log_error}".strip()


KNOWLEDGE_INDEX = "knowledge:index"


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
    if "conclusive" in fields:
        mapping["conclusive"] = "true" if fields["conclusive"] else "false"
    if "last_modified_by" in fields:
        mapping["last_modified_by"] = fields["last_modified_by"]
        mapping["last_modified_at"] = str(int(time.time()))
    if mapping:
        rdb.hset(f"knowledge:entry:{key}", mapping=mapping)
    return True


def delete_knowledge_entry(rdb: redis_lib.Redis, key: str) -> str:
    if not rdb.exists(f"knowledge:entry:{key}"):
        return "not_found"
    if list_history_entries_for_knowledge(rdb, key):
        return "has_history"
    rdb.delete(f"knowledge:entry:{key}")
    rdb.srem(KNOWLEDGE_INDEX, key)
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
    if "last_modified_by" in fields:
        mapping["last_modified_by"] = fields["last_modified_by"]
        mapping["last_modified_at"] = str(int(time.time()))
    if mapping:
        rdb.hset(f"history:entry:{hid}", mapping=mapping)
    return True


def delete_history_entry(rdb: redis_lib.Redis, hid: str) -> bool:
    if not rdb.exists(f"history:entry:{hid}"):
        return False
    rdb.delete(f"history:entry:{hid}")
    rdb.srem(HISTORY_INDEX, hid)
    return True


KNOWLEDGE_MATCH_THRESHOLD = 0.5


def _derive_reason_signal(facts: dict) -> str:
    """Normalize the many facts fields that can indicate a K8s failure state into one
    comparable string. Priority order favors the most specific signal (an init-container
    failure is more precise than the generic 'PodInitializing' state it also produces on
    the main container). See D4 in the knowledge/history redesign spec.

    "Unknown" is a genuine kube-state-metrics enum value for last_terminated_reason —
    it means the container runtime couldn't classify the exit reason, not a real signal —
    so it's treated the same as empty and falls through to the next check."""
    if facts.get("init_last_terminated_reason") and facts["init_last_terminated_reason"] != "Unknown":
        return f"Init:{facts['init_last_terminated_reason']}"
    if facts.get("init_waiting_reason"):
        return f"Init:{facts['init_waiting_reason']}"
    if facts.get("last_terminated_reason") and facts["last_terminated_reason"] != "Unknown":
        return facts["last_terminated_reason"]
    if facts.get("waiting_reason"):
        wr = facts["waiting_reason"]
        return "ImagePullBackOff" if wr == "ErrImagePull" else wr
    if facts.get("event_reason"):
        return facts["event_reason"]
    if facts.get("pods_available", 0) == 0 and facts.get("pods_desired", 0) > 0:
        return "ZeroReplicas"
    return ""


def _token_coverage(query: str, text: str) -> float:
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return 0.0
    t_tokens = set(_tokenize(text))
    return len(q_tokens & t_tokens) / len(q_tokens)


def find_trusted_match(rdb: redis_lib.Redis, facts: dict, query: str) -> dict | None:
    signal            = _derive_reason_signal(facts)
    knowledge_entries = list_knowledge_entries(rdb)

    # Step 1 — deterministic short-circuit (exact-match, conclusive only)
    if signal:
        for entry in knowledge_entries:
            if entry.get("trigger_waiting_reason") == signal and entry.get("conclusive"):
                return {
                    "source":             "knowledge",
                    "knowledge_key":      entry["key"],
                    "root_cause_pattern": entry["root_cause_pattern"],
                    "fix_action":         entry["fix_action"],
                    "context_notes":      "",
                }

    # Step 2 — combined candidate pool
    candidates = []
    for h in list_all_history_entries(rdb):
        candidates.append((_token_coverage(query, h.get("symptom", "")), "history", h))
    if signal:
        for entry in knowledge_entries:
            if entry.get("trigger_waiting_reason") == signal and not entry.get("conclusive"):
                candidates.append(
                    (_token_coverage(query, entry.get("root_cause_pattern", "")), "knowledge", entry))

    # Step 3 — threshold floor
    candidates = [c for c in candidates if c[0] >= KNOWLEDGE_MATCH_THRESHOLD]
    if not candidates:
        return None

    # Step 4 — resolve highest scorer
    candidates.sort(key=lambda c: c[0], reverse=True)
    _, kind, obj = candidates[0]
    if kind == "history":
        k = get_knowledge_entry(rdb, obj["knowledge_key"])
        if not k:
            return None
        return {
            "source":             "history",
            "knowledge_key":      obj["knowledge_key"],
            "root_cause_pattern": k["root_cause_pattern"],
            "fix_action":         k["fix_action"],
            "context_notes":      obj.get("context_notes", ""),
        }
    return {
        "source":             "knowledge",
        "knowledge_key":      obj["key"],
        "root_cause_pattern": obj["root_cause_pattern"],
        "fix_action":         obj["fix_action"],
        "context_notes":      "",
    }


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
                                conclusive: bool = False) -> str | None:
    suggestion = get_pending_suggestion(rdb, pending_id)
    if suggestion is None:
        return None

    if mode == "new":
        store_knowledge_entry(rdb, {
            "key":                    knowledge_key,
            "root_cause_pattern":     root_cause_pattern or "",
            "fix_action":             fix_action or "",
            "trigger_waiting_reason": "",
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
    "pods_available", "pods_desired", "waiting_reason", "last_terminated_reason",
    "restarts", "init_waiting_reason", "init_last_terminated_reason", "init_restarts",
    "log_error", "event_reason", "event_message", "event_object",
]


def _evidence_snapshot(occurrence: dict) -> dict:
    return {f: occurrence.get(f, "") for f in _INCIDENT_EVIDENCE_FIELDS}


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
            mapping = {f: occurrence.get(f, "") for f in _INCIDENT_EVIDENCE_FIELDS}
            mapping.update({
                "root_cause":     occurrence.get("root_cause", ""),
                "dev_action":     occurrence.get("dev_action", ""),
                "kubectl_hint":   occurrence.get("kubectl_hint", ""),
                "low_confidence": "true" if occurrence.get("low_confidence") else "false",
                "template_diff":  json.dumps(occurrence.get("template_diff")),
                "dependency":     json.dumps(occurrence.get("dependency")),
            })
            rdb.hset(f"incident:{oid_str}", mapping=mapping)
            append_incident_timeline(rdb, oid_str, {
                "type": "fired", "timestamp": int(time.time()),
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
        "template_diff":  json.dumps(occurrence.get("template_diff")),
        "dependency":     json.dumps(occurrence.get("dependency")),
        "status":       "open",
        "resolved_at":  "",
        "resolved_by":  "",
    }
    mapping.update(_evidence_snapshot(occurrence))
    rdb.hset(f"incident:{iid}", mapping=mapping)
    rdb.sadd(INDEX_KEY, iid)
    rdb.sadd(OPEN_INDEX, iid)
    append_incident_timeline(rdb, iid, {
        "type": "fired", "timestamp": int(time.time()),
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
    for f in ("pods_available", "pods_desired", "restarts", "init_restarts"):
        d[f] = int(d.get(f) or 0)
    d["template_diff"] = json.loads(d["template_diff"]) if "template_diff" in d else None
    d["dependency"] = json.loads(d["dependency"]) if "dependency" in d else None
    return d


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


