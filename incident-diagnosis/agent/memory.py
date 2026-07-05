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


def retrieve_similar(rdb: redis_lib.Redis, query: str, top_k: int = 3) -> list:
    scored = _score_all(rdb, query)
    return [item for _, item in scored[:top_k]]


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


def search_memory(rdb: redis_lib.Redis, query: str, limit: int = 3) -> str:
    scored = _score_all(rdb, query)
    if not scored:
        return "no relevant memory found"

    diverse = _diversify(scored, limit)
    lines = []
    for i, (score, inc) in enumerate(diverse, 1):
        lines.append(
            f"[{i}] (similarity: {score:.2f}) {inc['alert_name']} on {inc['service']} → "
            f"root cause: {inc['root_cause']} → "
            f"{inc.get('kubectl_hint') or 'no action'}"
        )
    return "\n".join(lines)
