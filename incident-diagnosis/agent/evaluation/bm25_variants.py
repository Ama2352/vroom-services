from dataclasses import dataclass

import memory
from evaluation.models import (
    RankedCandidate,
    RetrievalCase,
    RetrievalOutcome,
    VariantConfig,
)


@dataclass(frozen=True)
class _Document:
    knowledge: dict
    source: str
    source_id: str
    text: str
    context_notes: str = ""


def collect_canonical_signals(facts: dict) -> frozenset[str]:
    signals = set()

    init_last = facts.get("init_last_terminated_reason")
    if init_last and init_last != "Unknown":
        signals.add(f"Init:{init_last}")
    init_waiting = facts.get("init_waiting_reason")
    if init_waiting:
        signals.add(f"Init:{init_waiting}")

    last = facts.get("last_terminated_reason")
    if last and last != "Unknown":
        signals.add(last)
    waiting = facts.get("waiting_reason")
    if waiting:
        signals.add("ImagePullBackOff" if waiting == "ErrImagePull" else waiting)

    if facts.get("pods_available", 0) == 0 and facts.get("pods_desired", 0) > 0:
        signals.add("ZeroReplicas")

    dependency = facts.get("dependency")
    if isinstance(dependency, dict) and dependency.get("name"):
        name = dependency["name"]
        if dependency.get("pods_desired") == 0:
            signals.add(f"Dependency:{name}:ZeroReplicas")
        if dependency.get("waiting_reason"):
            signals.add(f"Dependency:{name}:{dependency['waiting_reason']}")
        if (
            dependency.get("pods_desired") is not None
            and dependency.get("pods_available") != dependency.get("pods_desired")
        ):
            signals.add(f"Dependency:{name}:Unhealthy")

    if facts.get("event_reason"):
        signals.add(facts["event_reason"])
    return frozenset(signals)


def build_query(case: RetrievalCase, variant: str) -> str:
    if variant not in {"baseline", "rich"}:
        raise ValueError(f"unknown query variant: {variant}")
    facts = case.facts
    parts = [
        case.alert_name,
        facts.get("waiting_reason", ""),
        facts.get("log_error", ""),
    ]
    if variant == "rich":
        parts.extend([
            facts.get("last_terminated_reason", ""),
            facts.get("init_waiting_reason", ""),
            facts.get("init_last_terminated_reason", ""),
            facts.get("event_reason", ""),
            facts.get("event_message", ""),
        ])
        dependency = facts.get("dependency")
        if isinstance(dependency, dict):
            parts.extend([
                dependency.get("name", ""),
                dependency.get("waiting_reason", ""),
            ])
        template_diff = facts.get("template_diff")
        if isinstance(template_diff, dict):
            for diff in template_diff.get("env_diff", []):
                parts.extend([
                    diff.get("key", ""),
                    diff.get("old_value", ""),
                    diff.get("new_value", ""),
                ])
            parts.extend([
                template_diff.get("old_image", ""),
                template_diff.get("new_image", ""),
            ])
    return " ".join(str(part) for part in parts if part).strip()


def _knowledge_document(entry: dict) -> _Document:
    return _Document(
        knowledge=entry,
        source="knowledge",
        source_id=entry["key"],
        text=" ".join(filter(None, (
            entry.get("trigger_waiting_reason", ""),
            entry.get("root_cause_pattern", ""),
        ))),
    )


def _documents(rdb, history_variant: str) -> list[_Document]:
    if history_variant not in {"plain", "joined"}:
        raise ValueError(f"unknown history variant: {history_variant}")
    knowledge_entries = {
        entry["key"]: entry for entry in memory.list_knowledge_entries(rdb)
    }
    documents = [_knowledge_document(entry) for entry in knowledge_entries.values()]
    for history in memory.list_all_history_entries(rdb):
        knowledge = knowledge_entries.get(history.get("knowledge_key"))
        if not knowledge:
            continue
        parts = [
            history.get("symptom", ""),
            history.get("context_notes", ""),
        ]
        if history_variant == "joined":
            parts.append(knowledge.get("root_cause_pattern", ""))
        documents.append(_Document(
            knowledge=knowledge,
            source="history",
            source_id=history["id"],
            text=" ".join(filter(None, parts)),
            context_notes=history.get("context_notes", ""),
        ))
    return documents


def _candidate(document: _Document, score: float, matched_terms: tuple[str, ...]):
    knowledge = document.knowledge
    return RankedCandidate(
        knowledge_key=knowledge["key"],
        score=float(score),
        source=document.source,
        source_id=document.source_id,
        matched_terms=matched_terms,
        root_cause_pattern=knowledge["root_cause_pattern"],
        fix_action=knowledge["fix_action"],
        context_notes=document.context_notes,
        document_text=document.text,
    )


def generate_bm25_candidates(
    rdb,
    case: RetrievalCase,
    config: VariantConfig,
    limit: int = 8,
) -> RetrievalOutcome:
    if limit < 1:
        raise ValueError("limit must be positive")
    if config.query_variant not in {"baseline", "rich"}:
        raise ValueError(f"unknown query variant: {config.query_variant}")
    if config.history_variant not in {"plain", "joined"}:
        raise ValueError(f"unknown history variant: {config.history_variant}")

    knowledge_entries = memory.list_knowledge_entries(rdb)
    signals = collect_canonical_signals(case.facts)
    exact = {
        entry["key"]: entry
        for entry in knowledge_entries
        if entry.get("conclusive")
        and entry.get("trigger_waiting_reason") in signals
    }
    if len(exact) == 1:
        knowledge = next(iter(exact.values()))
        document = _knowledge_document(knowledge)
        return RetrievalOutcome(
            mode="exact",
            candidates=(_candidate(document, 1.0, ()),),
        )
    exact_ambiguous = len(exact) > 1

    documents = _documents(rdb, config.history_variant)
    tokenized_documents = [memory._tokenize(document.text) for document in documents]
    query_tokens = list(dict.fromkeys(
        memory._tokenize(build_query(case, config.query_variant))
    ))
    if not documents or not query_tokens:
        return RetrievalOutcome("none", (), exact_ambiguous)
    scores = memory._BM25(tokenized_documents).get_scores(query_tokens)
    ranked = []
    query_terms = set(query_tokens)
    for document, document_tokens, score in zip(
        documents, tokenized_documents, scores
    ):
        raw_score = float(score)
        if raw_score <= 0:
            continue
        matched_terms = tuple(sorted(query_terms.intersection(document_tokens)))
        ranked.append(_candidate(document, raw_score, matched_terms))
    ranked.sort(key=lambda item: (
        -item.score,
        item.knowledge_key,
        item.source_id,
    ))
    collapsed = {}
    for candidate in ranked:
        collapsed.setdefault(candidate.knowledge_key, candidate)
    candidates = tuple(collapsed.values())[:limit]
    return RetrievalOutcome(
        mode="advisory" if candidates else "none",
        candidates=candidates,
        exact_ambiguous=exact_ambiguous,
    )


def rank_bm25(rdb, case: RetrievalCase, config: VariantConfig) -> RetrievalOutcome:
    raw = generate_bm25_candidates(rdb, case, config, limit=8)
    if raw.mode == "exact":
        return raw
    candidates = tuple(
        candidate for candidate in raw.candidates
        if candidate.score >= config.threshold
    )[:3]
    return RetrievalOutcome(
        mode="advisory" if candidates else "none",
        candidates=candidates,
        exact_ambiguous=raw.exact_ambiguous,
    )
