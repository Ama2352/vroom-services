import json, time, uuid, os
import re
import numpy as np
import redis as redis_lib
from fastembed import TextEmbedding

_MODEL = None
INDEX_KEY = "incidents:index"

# Provisional sanity floor on raw cosine similarity — excludes only near-orthogonal
# (obviously unrelated) matches. Not a precisely-tuned relevance boundary; revisit
# after real score distributions can be observed post query/embedding-symmetry fix.
# See docs/superpowers/specs/2026-07-04-incident-agent-memory-retrieval-fix-design.md (D6).
FLOOR = float(os.environ.get("MEMORY_SCORE_FLOOR", "0.15"))


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list:
    return _TOKEN_RE.findall(text.lower())


def _model() -> TextEmbedding:
    global _MODEL
    if _MODEL is None:
        _MODEL = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
    return _MODEL


def connect(url: str) -> redis_lib.Redis:
    return redis_lib.from_url(url)


def _encode(text: str) -> list:
    return next(_model().embed([text])).tolist()


def _recency_score(timestamp: int) -> float:
    age_days = (time.time() - timestamp) / 86400.0
    return max(0.0, 1.0 - age_days / 7.0)


def _get_field(raw: dict, k: str) -> str:
    v = raw.get(k) or raw.get(k.encode() if isinstance(k, str) else k)
    return v.decode() if isinstance(v, bytes) else (v or "")


def build_symptom_text(alert_name: str, service: str,
                        waiting_reason: str = "", log_error: str = "") -> str:
    return f"{alert_name} {service} {waiting_reason} {log_error}".strip()


def store_incident(rdb: redis_lib.Redis, incident: dict) -> str:
    iid = str(uuid.uuid4())
    query_text = build_symptom_text(
        incident["alert_name"], incident["service"],
        incident.get("waiting_reason", ""), incident.get("log_error", ""),
    )
    embedding = _encode(query_text)
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
        "embedding":      json.dumps(embedding),
    })
    rdb.sadd(INDEX_KEY, iid)
    return iid


def _score_all(rdb: redis_lib.Redis, query: str) -> list:
    keys = rdb.smembers(INDEX_KEY)
    if not keys:
        return []

    q_emb = np.array(_encode(query))
    scored = []
    for key in keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        raw = rdb.hgetall(f"incident:{key_str}")
        if not raw:
            continue
        emb = np.array(json.loads(_get_field(raw, "embedding")))
        norm = np.linalg.norm(q_emb) * np.linalg.norm(emb)
        cos_sim = float(np.dot(q_emb, emb) / (norm + 1e-9))
        ts      = int(_get_field(raw, "timestamp") or 0)
        outcome = _get_field(raw, "outcome")
        score   = (0.6 * cos_sim
                 + 0.3 * _recency_score(ts)
                 + 0.1 * (1.0 if outcome == "resolved" else 0.5))
        scored.append((score, cos_sim, {
            "alert_name":       _get_field(raw, "alert_name"),
            "service":          _get_field(raw, "service"),
            "root_cause":       _get_field(raw, "root_cause"),
            "remediation_tool": _get_field(raw, "remediation_tool"),
            "outcome":          outcome,
            "timestamp":        ts,
        }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def retrieve_similar(rdb: redis_lib.Redis, query: str, top_k: int = 3) -> list:
    scored = _score_all(rdb, query)
    return [item for _, _, item in scored[:top_k]]


RUNBOOK_INDEX = "runbook:index"


def store_runbook_entry(rdb: redis_lib.Redis, entry: dict) -> str:
    eid = str(uuid.uuid4())
    query_text = f"{entry.get('title', '')} {entry.get('service', '')} {entry.get('symptom', '')}"
    embedding  = _encode(query_text)
    rdb.hset(f"runbook:entry:{eid}", mapping={
        "title":       entry.get("title", ""),
        "service":     entry.get("service", ""),
        "symptom":     entry.get("symptom", ""),
        "root_cause":  entry.get("root_cause", ""),
        "fix_command": entry.get("fix_command", ""),
        "source":      entry.get("source", "learned"),
        "timestamp":   str(int(time.time())),
        "embedding":   json.dumps(embedding),
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
    q_emb  = np.array(_encode(query))
    scored = []
    for key in keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        raw     = rdb.hgetall(f"runbook:entry:{key_str}")
        if not raw:
            continue
        emb_raw = _get_field(raw, "embedding")
        if not emb_raw:
            continue
        emb  = np.array(json.loads(emb_raw))
        norm = np.linalg.norm(q_emb) * np.linalg.norm(emb)
        cos  = float(np.dot(q_emb, emb) / (norm + 1e-9))
        if cos <= FLOOR:
            continue
        scored.append((cos, {
            "title":       _get_field(raw, "title"),
            "service":     _get_field(raw, "service"),
            "symptom":     _get_field(raw, "symptom"),
            "root_cause":  _get_field(raw, "root_cause"),
            "fix_command": _get_field(raw, "fix_command"),
            "source":      _get_field(raw, "source"),
            "score":       cos,
        }))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


def search_memory(rdb: redis_lib.Redis, query: str, limit: int = 3) -> str:
    scored = _score_all(rdb, query)
    if not scored:
        return "no relevant memory found"

    filtered = [(score, cos_sim, item) for score, cos_sim, item in scored
                if cos_sim > FLOOR][:limit]
    if not filtered:
        return "no relevant memory found"

    lines = []
    for i, (score, cos_sim, inc) in enumerate(filtered, 1):
        lines.append(
            f"[{i}] (similarity: {cos_sim:.2f}) {inc['alert_name']} on {inc['service']} → "
            f"root cause: {inc['root_cause']} → "
            f"{inc.get('remediation_tool') or 'no action'} → {inc['outcome']}"
        )
    return "\n".join(lines)
