from pathlib import Path

import fakeredis

import memory
import seed
from evaluation.models import RankedCandidate, RetrievalCase, RetrievalOutcome


RUNBOOKS_DIR = Path(__file__).resolve().parents[1] / "runbooks"


def _stabilize_seeded_history_ids(rdb: fakeredis.FakeRedis) -> None:
    histories = memory.list_all_history_entries(rdb)
    histories.sort(key=lambda entry: tuple(
        entry.get(field, "")
        for field in (
            "knowledge_key",
            "service",
            "symptom",
            "context_notes",
            "source",
            "created_by",
            "timestamp",
            "last_modified_by",
            "last_modified_at",
        )
    ))
    for history in histories:
        rdb.delete(f"history:entry:{history['id']}")
    rdb.delete(memory.HISTORY_INDEX)
    for ordinal, history in enumerate(histories, start=1):
        history_id = f"fixture-history-{ordinal:04d}"
        rdb.hset(
            f"history:entry:{history_id}",
            mapping={
                key: value
                for key, value in history.items()
                if key != "id"
            },
        )
        rdb.sadd(memory.HISTORY_INDEX, history_id)


def seed_store() -> fakeredis.FakeRedis:
    rdb = fakeredis.FakeRedis()
    seed.seed_if_empty(rdb, str(RUNBOOKS_DIR))
    _stabilize_seeded_history_ids(rdb)
    return rdb


def _candidate(
    knowledge: dict,
    score: float,
    source: str,
    source_id: str,
    context_notes: str = "",
) -> RankedCandidate:
    return RankedCandidate(
        knowledge_key=knowledge["key"],
        score=float(score),
        source=source,
        source_id=source_id,
        matched_terms=(),
        root_cause_pattern=knowledge["root_cause_pattern"],
        fix_action=knowledge["fix_action"],
        context_notes=context_notes,
    )


def rank_current_coverage(
    rdb: fakeredis.FakeRedis,
    case: RetrievalCase,
) -> RetrievalOutcome:
    query = memory.build_symptom_text(
        case.alert_name,
        case.facts.get("waiting_reason", ""),
        case.facts.get("log_error", ""),
    )
    signal = memory._derive_reason_signal(case.facts)
    knowledge_entries = memory.list_knowledge_entries(rdb)

    if signal:
        for entry in knowledge_entries:
            if (
                entry.get("trigger_waiting_reason") == signal
                and entry.get("conclusive")
            ):
                return RetrievalOutcome(
                    mode="exact",
                    candidates=(
                        _candidate(entry, 1.0, "knowledge", entry["key"]),
                    ),
                )

    candidates = []
    for history in memory.list_all_history_entries(rdb):
        score = memory._token_coverage(query, history.get("symptom", ""))
        if score < memory.KNOWLEDGE_MATCH_THRESHOLD:
            continue
        knowledge = memory.get_knowledge_entry(rdb, history["knowledge_key"])
        if not knowledge:
            continue
        candidates.append(
            _candidate(
                knowledge,
                score,
                "history",
                history["id"],
                history.get("context_notes", ""),
            )
        )

    if signal:
        for entry in knowledge_entries:
            if (
                entry.get("trigger_waiting_reason") == signal
                and not entry.get("conclusive")
            ):
                score = memory._token_coverage(
                    query, entry.get("root_cause_pattern", "")
                )
                if score >= memory.KNOWLEDGE_MATCH_THRESHOLD:
                    candidates.append(
                        _candidate(entry, score, "knowledge", entry["key"])
                    )

    candidates.sort(
        key=lambda candidate: (
            -candidate.score,
            candidate.knowledge_key,
            candidate.source_id,
        )
    )
    if not candidates:
        return RetrievalOutcome(mode="none", candidates=())
    return RetrievalOutcome(mode="advisory", candidates=tuple(candidates))
