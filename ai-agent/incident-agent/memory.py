import json, time, uuid, os, random
import numpy as np
import redis as redis_lib
from sentence_transformers import SentenceTransformer

_MODEL = None
INDEX_KEY  = "incidents:index"
FLOOR_KEY  = "memory:config:score_floor"
CLIFF_KEY  = "memory:config:cliff_gap"

DEFAULT_FLOOR     = float(os.environ.get("MEMORY_SCORE_FLOOR", "0.30"))
DEFAULT_CLIFF_GAP = float(os.environ.get("MEMORY_CLIFF_GAP",   "0.12"))


def _model() -> SentenceTransformer:
    global _MODEL
    if _MODEL is None:
        _MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _MODEL


def connect(url: str) -> redis_lib.Redis:
    return redis_lib.from_url(url)


def _encode(text: str) -> list:
    return _model().encode(text, convert_to_numpy=True).tolist()


def _recency_score(timestamp: int) -> float:
    age_days = (time.time() - timestamp) / 86400.0
    return max(0.0, 1.0 - age_days / 7.0)


def _get_field(raw: dict, k: str) -> str:
    v = raw.get(k) or raw.get(k.encode() if isinstance(k, str) else k)
    return v.decode() if isinstance(v, bytes) else (v or "")


def store_incident(rdb: redis_lib.Redis, incident: dict) -> str:
    iid = str(uuid.uuid4())
    query_text = f"{incident['alert_name']} {incident['service']} {incident.get('symptoms', '')}"
    embedding = _encode(query_text)
    rdb.hset(f"incident:{iid}", mapping={
        "alert_name":          incident["alert_name"],
        "service":             incident["service"],
        "namespace":           incident.get("namespace", ""),
        "symptoms":            incident.get("symptoms", ""),
        "investigation_steps": json.dumps(incident.get("investigation_steps", [])),
        "root_cause":          incident.get("root_cause", ""),
        "remediation_tool":    incident.get("remediation_tool", ""),
        "remediation_args":    json.dumps(incident.get("remediation_args", {})),
        "outcome":             incident.get("outcome", "resolved"),
        "timestamp":           str(int(time.time())),
        "embedding":           json.dumps(embedding),
    })
    rdb.sadd(INDEX_KEY, iid)
    recalibrate_thresholds(rdb)
    return iid


def recalibrate_thresholds(rdb: redis_lib.Redis) -> None:
    keys = list(rdb.smembers(INDEX_KEY))
    if len(keys) < 3:
        return

    sample_keys = random.sample(keys, min(50, len(keys)))
    embeddings = []
    for key in sample_keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        raw_emb = rdb.hget(f"incident:{key_str}", "embedding")
        if raw_emb:
            embeddings.append(np.array(json.loads(raw_emb)))

    if len(embeddings) < 3:
        return

    sims = []
    for i in range(len(embeddings)):
        for j in range(i + 1, len(embeddings)):
            a, b = embeddings[i], embeddings[j]
            norm = np.linalg.norm(a) * np.linalg.norm(b)
            if norm > 1e-9:
                sims.append(float(np.dot(a, b) / norm))

    if not sims:
        return

    sims_arr = np.array(sims)
    floor     = float(np.clip(np.percentile(sims_arr, 25), 0.20, 0.60))
    cliff_gap = float(np.clip(0.5 * np.std(sims_arr),     0.08, 0.25))

    rdb.set(FLOOR_KEY, str(floor))
    rdb.set(CLIFF_KEY, str(cliff_gap))


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
        scored.append((score, {
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
    return [item for _, item in scored[:top_k]]


def search_memory(rdb: redis_lib.Redis, query: str, limit: int = 3) -> str:
    scored = _score_all(rdb, query)
    if not scored:
        return "no relevant memory found"

    floor     = float(rdb.get(FLOOR_KEY) or DEFAULT_FLOOR)
    cliff_gap = float(rdb.get(CLIFF_KEY) or DEFAULT_CLIFF_GAP)

    scores = [s for s, _ in scored]
    cutoff = len(scores)
    if len(scores) > 1:
        gaps = [scores[i] - scores[i + 1] for i in range(len(scores) - 1)]
        if max(gaps) > cliff_gap:
            cutoff = gaps.index(max(gaps)) + 1

    filtered = [item for score, item in scored[:cutoff] if score > floor][:limit]
    if not filtered:
        return "no relevant memory found"

    lines = []
    for i, inc in enumerate(filtered, 1):
        lines.append(
            f"[{i}] {inc['alert_name']} on {inc['service']} → "
            f"root cause: {inc['root_cause']} → "
            f"{inc.get('remediation_tool') or 'no action'} → {inc['outcome']}"
        )
    return "\n".join(lines)
