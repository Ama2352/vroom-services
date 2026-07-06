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


def store_incident(rdb: redis_lib.Redis, incident: dict) -> str:
    iid = str(uuid.uuid4())
    rdb.hset(f"incident:{iid}", mapping={
        "alert_name":     incident["alert_name"],
        "service":        incident["service"],
        "namespace":      incident.get("namespace", ""),
        "symptoms":       incident.get("symptoms", ""),
        "waiting_reason": incident.get("waiting_reason", ""),
        "log_error":      incident.get("log_error", ""),
        "root_cause":     incident.get("root_cause", ""),
        "kubectl_hint":   incident.get("kubectl_hint", ""),
        "outcome":        incident.get("outcome", "acknowledged"),
        "timestamp":      str(int(time.time())),
    })
    rdb.sadd(INDEX_KEY, iid)
    return iid


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


RUNBOOK_INDEX = "runbook:index"


def store_runbook_entry(rdb: redis_lib.Redis, entry: dict) -> str:
    eid = str(uuid.uuid4())
    rdb.hset(f"runbook:entry:{eid}", mapping={
        "title":       entry.get("title", ""),
        "service":     entry.get("service", ""),
        "symptom":     entry.get("symptom", ""),
        "root_cause":  entry.get("root_cause", ""),
        "fix_command": entry.get("fix_command", ""),
        "source":      entry.get("source", "learned"),
        "timestamp":   str(int(time.time())),
    })
    rdb.sadd(RUNBOOK_INDEX, eid)
    return eid


def get_runbook_entries(rdb: redis_lib.Redis, limit: int = 100) -> list:
    keys = list(rdb.smembers(RUNBOOK_INDEX))
    entries = []
    for key in keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        raw = rdb.hgetall(f"runbook:entry:{key_str}")
        if not raw:
            continue
        entries.append({
            (k.decode() if isinstance(k, bytes) else k):
            (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw.items()
        })
    entries.sort(key=lambda x: int(x.get("timestamp", 0)))
    return entries[:limit]


def search_runbook(rdb: redis_lib.Redis, query: str, top_k: int = 3) -> list:
    keys = rdb.smembers(RUNBOOK_INDEX)
    if not keys:
        return []

    items, corpus_tokens = [], []
    for key in keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        raw = rdb.hgetall(f"runbook:entry:{key_str}")
        if not raw:
            continue
        text = f"{_get_field(raw, 'title')} {_get_field(raw, 'symptom')}"
        corpus_tokens.append(_tokenize(text))
        items.append({
            "title":       _get_field(raw, "title"),
            "service":     _get_field(raw, "service"),
            "symptom":     _get_field(raw, "symptom"),
            "root_cause":  _get_field(raw, "root_cause"),
            "fix_command": _get_field(raw, "fix_command"),
            "source":      _get_field(raw, "source"),
        })

    if not corpus_tokens:
        return []

    bm25       = _BM25(corpus_tokens)
    raw_scores = bm25.get_scores(_tokenize(query))
    max_score  = max(raw_scores)          # corpus_tokens non-empty here, so raw_scores is too
    if max_score <= 0:
        return []

    scored = []
    for s, item in zip(raw_scores, items):
        if s <= 0:
            continue
        item["score"] = s / max_score
        scored.append((item["score"], item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


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


_ROOT_CAUSE_OVERLAP_THRESHOLD = 0.6


def _is_same_lesson(incident_item: dict, runbook_item: dict) -> bool:
    if incident_item.get("service", "").strip().lower() != runbook_item.get("service", "").strip().lower():
        return False
    tokens_a = set(_tokenize(incident_item.get("root_cause", "")))
    tokens_b = set(_tokenize(runbook_item.get("root_cause", "")))
    if not tokens_a or not tokens_b:
        return False
    overlap = len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))
    return overlap >= _ROOT_CAUSE_OVERLAP_THRESHOLD


def dedupe_against_runbook(incident_items: list, runbook_hits: list) -> list:
    return [
        inc for inc in incident_items
        if not any(_is_same_lesson(inc, rb) for rb in runbook_hits)
    ]
