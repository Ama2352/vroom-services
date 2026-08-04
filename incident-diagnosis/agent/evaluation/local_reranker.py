from time import perf_counter
from typing import Protocol, TypeAlias

from evaluation.models import RetrievalCase, RetrievalOutcome
from evaluation.serialization import serialize_candidate, serialize_incident
from evaluation.tournament_models import CandidateDecision, DecisionTrace


class ScoringBackend(Protocol):
    def score(self, query: str, documents: tuple[str, ...]) -> tuple[float, ...]: ...


RerankBatch: TypeAlias = tuple[RetrievalCase, RetrievalOutcome]


def rerank_local(
    batch: RerankBatch,
    backend: ScoringBackend,
    floor: float,
    limit: int = 3,
) -> DecisionTrace:
    if limit < 1:
        raise ValueError("limit must be positive")

    incident, outcome = batch
    if outcome.mode == "exact":
        decisions = tuple(
            CandidateDecision(
                knowledge_key=candidate.knowledge_key,
                accepted=True,
                score=None,
                reason="exact retrieval bypassed local reranking",
            )
            for candidate in outcome.candidates
        )
        return DecisionTrace(outcome=outcome, decisions=decisions)
    if not outcome.candidates:
        return DecisionTrace(outcome=outcome)

    query = serialize_incident(incident)
    documents = tuple(serialize_candidate(candidate) for candidate in outcome.candidates)
    started = perf_counter()
    scores = backend.score(query, documents)
    latency_ms = (perf_counter() - started) * 1000
    if len(scores) != len(outcome.candidates):
        raise ValueError("reranker score count does not match candidate count")

    scored = tuple(zip(outcome.candidates, scores))
    decisions = tuple(
        CandidateDecision(
            knowledge_key=candidate.knowledge_key,
            accepted=float(score) >= floor,
            score=float(score),
            reason=(
                f"reranker score {float(score):.6f} "
                f"{'meets' if float(score) >= floor else 'is below'} floor {floor:.6f}"
            ),
        )
        for candidate, score in scored
    )
    ordered = sorted(
        scored,
        key=lambda item: (
            -float(item[1]), -item[0].score,
            item[0].knowledge_key, item[0].source_id,
        ),
    )
    selected = []
    seen_keys = set()
    for candidate, score in ordered:
        if float(score) < floor or candidate.knowledge_key in seen_keys:
            continue
        seen_keys.add(candidate.knowledge_key)
        selected.append(candidate)
        if len(selected) == limit:
            break

    reranked = RetrievalOutcome(
        mode="advisory" if selected else "none",
        candidates=tuple(selected),
        exact_ambiguous=outcome.exact_ambiguous,
    )
    return DecisionTrace(reranked, decisions, latency_ms)
