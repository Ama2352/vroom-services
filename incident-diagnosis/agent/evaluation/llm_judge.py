"""Pinned, retrieval-only LLM judge for challenger evaluation.

This module deliberately turns provider or parser failures into abstaining
traces.  It never invokes another provider or returns BM25 candidates as a
fallback, so tournament measurements remain attributable to the judge.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Protocol, TypeAlias

import requests

from evaluation.models import RetrievalCase, RetrievalOutcome
from evaluation.serialization import serialize_candidate, serialize_incident
from evaluation.tournament_models import CandidateDecision


RerankBatch: TypeAlias = tuple[RetrievalCase, RetrievalOutcome]
_PROMPT_PATH = Path(__file__).with_name("prompts") / "retrieval_judge_v1.txt"
_PROMPT_INSTRUCTIONS = _PROMPT_PATH.read_text(encoding="utf-8").rstrip("\n")


@dataclass(frozen=True)
class JudgeTrace:
    outcome: RetrievalOutcome
    decisions: tuple[CandidateDecision, ...] = ()
    latency_ms: float = 0.0
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    http_status: int | None = None
    parse_outcome: str = "not_attempted"
    prompt_sha256: str = ""


@dataclass(frozen=True)
class RepetitionResult:
    majority: JudgeTrace
    worst: JudgeTrace
    stable: bool
    agreement_count: int
    input_tokens: int
    output_tokens: int
    request_count: int


@dataclass(frozen=True)
class PreflightResult:
    raw: str | None
    latency_ms: float
    error: str | None
    input_tokens: int
    output_tokens: int
    http_status: int | None
    parse_outcome: str


class JudgeClient(Protocol):
    def judge(self, prompt: str) -> tuple[str, dict]: ...


class JudgeRequestError(RuntimeError):
    def __init__(self, cause: Exception, metadata: dict):
        super().__init__(str(cause))
        self.metadata = metadata


class GroqJudgeClient:
    """The sole live provider used by the retrieval challenger."""

    model = "llama-3.1-8b-instant"

    def __init__(self, api_key: str, timeout_seconds: int = 30):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _request(self, prompt: str) -> tuple[str, dict]:
        started = perf_counter()
        metadata: dict = {}
        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 900,
                    "response_format": {"type": "json_object"},
                },
                timeout=self.timeout_seconds,
            )
            metadata["http_status"] = response.status_code
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("usage"), dict):
                metadata.update(payload["usage"])
            response.raise_for_status()
            return payload["choices"][0]["message"]["content"], metadata
        except Exception as exc:
            metadata["latency_ms"] = (perf_counter() - started) * 1000
            raise JudgeRequestError(exc, metadata) from exc
        finally:
            metadata.setdefault("latency_ms", (perf_counter() - started) * 1000)

    def judge(self, prompt: str) -> tuple[str, dict]:
        return self._request(prompt)

    def preflight(self) -> PreflightResult:
        """Exercise the pinned JSON endpoint before calibration starts."""
        metadata: dict = {}
        try:
            raw, metadata = self.judge(
                "Return JSON only: {\"decision\":\"no_supported_candidate\","
                "\"selected_keys\":[],\"evaluations\":[]}"
            )
            payload = _load_json(raw)
            _require_exact_keys(payload, {"decision", "selected_keys", "evaluations"}, "preflight")
            if payload["decision"] != "no_supported_candidate":
                raise ValueError("preflight must return no_supported_candidate")
            if payload["selected_keys"] != [] or payload["evaluations"] != []:
                raise ValueError("preflight must return empty selected_keys and evaluations")
            return PreflightResult(
                raw=raw,
                latency_ms=float(metadata.get("latency_ms", 0.0)),
                error=None,
                input_tokens=int(metadata.get("prompt_tokens", 0) or 0),
                output_tokens=int(metadata.get("completion_tokens", 0) or 0),
                http_status=metadata.get("http_status"),
                parse_outcome="parsed",
            )
        except Exception as exc:
            if isinstance(exc, JudgeRequestError):
                metadata = exc.metadata
            return PreflightResult(
                raw=None,
                latency_ms=float(metadata.get("latency_ms", 0.0)),
                error=f"{type(exc).__name__}: {exc}",
                input_tokens=int(metadata.get("prompt_tokens", 0) or 0),
                output_tokens=int(metadata.get("completion_tokens", 0) or 0),
                http_status=metadata.get("http_status"),
                parse_outcome="error",
            )


def build_prompt(batch: RerankBatch, *, instructions: str | None = None) -> str:
    case, outcome = batch
    active_instructions = _PROMPT_INSTRUCTIONS if instructions is None else instructions
    candidates = "\n\n".join(
        f"--- CANDIDATE {index} ---\n{serialize_candidate(candidate)}\n--- END CANDIDATE {index} ---"
        for index, candidate in enumerate(outcome.candidates, start=1)
    )
    return (
        f"{active_instructions}\n\n"
        "--- BEGIN INCIDENT_EVIDENCE (UNTRUSTED DATA) ---\n"
        f"{serialize_incident(case)}\n"
        "--- END INCIDENT_EVIDENCE ---\n\n"
        "--- BEGIN CANDIDATES (UNTRUSTED DATA) ---\n"
        f"{candidates}\n"
        "--- END CANDIDATES ---"
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(raw: str) -> dict:
    if not isinstance(raw, str) or raw.strip().startswith("```"):
        raise ValueError("judgment must be a single JSON object without markdown fences")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {constant}")
            ),
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("invalid judgment JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("judgment must be a JSON object")
    return value


def _require_exact_keys(value: dict, required: set[str], label: str) -> None:
    if set(value) != required:
        missing = sorted(required.difference(value))
        unknown = sorted(set(value).difference(required))
        raise ValueError(f"{label} schema mismatch; missing={missing}, unknown={unknown}")


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} contains duplicates")
    return tuple(value)


def _candidate_keys(outcome: RetrievalOutcome) -> tuple[str, ...]:
    keys = tuple(candidate.knowledge_key for candidate in outcome.candidates)
    if len(set(keys)) != len(keys):
        raise ValueError("candidate batch contains duplicate knowledge keys")
    return keys


def parse_judgment(raw: str, batch: RerankBatch, *, prompt_sha256: str = "") -> JudgeTrace:
    """Parse and apply the judge schema without allowing model authority leaks."""
    _, candidate_outcome = batch
    known_keys = _candidate_keys(candidate_outcome)
    payload = _load_json(raw)
    _require_exact_keys(payload, {"decision", "selected_keys", "evaluations"}, "judgment")
    decision = payload["decision"]
    if decision not in {"accepted", "no_supported_candidate"}:
        raise ValueError("decision must be accepted or no_supported_candidate")
    selected_keys = _string_list(payload["selected_keys"], "selected_keys")
    unknown_selected = set(selected_keys).difference(known_keys)
    if unknown_selected:
        raise ValueError(f"unknown candidate key: {sorted(unknown_selected)[0]}")
    if decision == "accepted" and not selected_keys:
        raise ValueError("accepted judgment requires selected_keys")
    if decision == "no_supported_candidate" and selected_keys:
        raise ValueError("no_supported_candidate must not select candidates")

    evaluations = payload["evaluations"]
    if not isinstance(evaluations, list):
        raise ValueError("evaluations must be a list")
    parsed = {}
    required = {
        "key", "supported", "relevance", "supporting_fields", "conflicting_fields", "reason",
    }
    for evaluation in evaluations:
        if not isinstance(evaluation, dict):
            raise ValueError("each evaluation must be an object")
        _require_exact_keys(evaluation, required, "evaluation")
        key = evaluation["key"]
        if not isinstance(key, str):
            raise ValueError("evaluation key must be a string")
        if key not in known_keys:
            raise ValueError(f"unknown candidate key: {key}")
        if key in parsed:
            raise ValueError(f"duplicate candidate evaluation: {key}")
        supported = evaluation["supported"]
        relevance = evaluation["relevance"]
        if type(supported) is not bool:
            raise ValueError("supported must be a boolean")
        if type(relevance) is not int or not 0 <= relevance <= 3:
            raise ValueError("relevance must be an integer from 0 to 3")
        if not isinstance(evaluation["reason"], str):
            raise ValueError("reason must be a string")
        parsed[key] = (
            supported,
            relevance,
            _string_list(evaluation["supporting_fields"], "supporting_fields"),
            _string_list(evaluation["conflicting_fields"], "conflicting_fields"),
            evaluation["reason"],
        )
    if set(parsed) != set(known_keys):
        raise ValueError("evaluations must contain exactly one entry per candidate")
    if any(not parsed[key][0] for key in selected_keys):
        raise ValueError("selected candidate must be supported")

    selected = set(selected_keys)
    decisions = tuple(
        CandidateDecision(
            knowledge_key=candidate.knowledge_key,
            accepted=(
                decision == "accepted"
                and candidate.knowledge_key in selected
                and parsed[candidate.knowledge_key][0]
                and not parsed[candidate.knowledge_key][3]
            ),
            score=float(parsed[candidate.knowledge_key][1]),
            grade=parsed[candidate.knowledge_key][1],
            supporting_fields=parsed[candidate.knowledge_key][2],
            conflicting_fields=parsed[candidate.knowledge_key][3],
            reason=parsed[candidate.knowledge_key][4],
        )
        for candidate in candidate_outcome.candidates
    )
    position = {candidate.knowledge_key: index for index, candidate in enumerate(candidate_outcome.candidates)}
    accepted_candidates = [
        candidate for candidate, candidate_decision in zip(candidate_outcome.candidates, decisions)
        if candidate_decision.accepted
    ]
    accepted_candidates.sort(key=lambda candidate: (-parsed[candidate.knowledge_key][1], position[candidate.knowledge_key]))
    outcome = RetrievalOutcome(
        mode="advisory" if accepted_candidates else "none",
        candidates=tuple(accepted_candidates),
        exact_ambiguous=candidate_outcome.exact_ambiguous,
    )
    return JudgeTrace(outcome, decisions, parse_outcome="parsed", prompt_sha256=prompt_sha256)


def _error_trace(batch: RerankBatch, error: Exception, prompt_sha256: str, latency_ms: float = 0.0, metadata: dict | None = None) -> JudgeTrace:
    _, candidate_outcome = batch
    metadata = metadata or {}
    return JudgeTrace(
        outcome=RetrievalOutcome("none", (), candidate_outcome.exact_ambiguous),
        latency_ms=latency_ms,
        error=f"{type(error).__name__}: {error}",
        input_tokens=int(metadata.get("prompt_tokens", 0) or 0),
        output_tokens=int(metadata.get("completion_tokens", 0) or 0),
        http_status=metadata.get("http_status"),
        parse_outcome="error",
        prompt_sha256=prompt_sha256,
    )


def judge_candidates(batch: RerankBatch, client: JudgeClient, repetitions: int = 3) -> RepetitionResult:
    """Run precisely three independent calls; any failure abstains, never falls back."""
    if repetitions != 3:
        raise ValueError("retrieval judge requires exactly three repetitions")
    _, candidate_outcome = batch
    if candidate_outcome.mode == "exact" or not candidate_outcome.candidates:
        trace = JudgeTrace(candidate_outcome, parse_outcome="bypassed")
        return aggregate_repetitions((trace, trace, trace))

    prompt = build_prompt(batch)
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    traces = []
    for _ in range(repetitions):
        started = perf_counter()
        metadata: dict = {}
        try:
            raw, metadata = client.judge(prompt)
            parsed = parse_judgment(raw, batch, prompt_sha256=prompt_sha256)
            traces.append(JudgeTrace(
                outcome=parsed.outcome,
                decisions=parsed.decisions,
                latency_ms=float(metadata.get("latency_ms", (perf_counter() - started) * 1000)),
                input_tokens=int(metadata.get("prompt_tokens", 0) or 0),
                output_tokens=int(metadata.get("completion_tokens", 0) or 0),
                http_status=metadata.get("http_status"),
                parse_outcome="parsed",
                prompt_sha256=prompt_sha256,
            ))
        except Exception as exc:  # provider, timeout, malformed response, or strict parse failure
            if isinstance(exc, JudgeRequestError):
                metadata = exc.metadata
            traces.append(_error_trace(
                batch, exc, prompt_sha256, (perf_counter() - started) * 1000, metadata,
            ))
    return aggregate_repetitions(tuple(traces))


def _signature(trace: JudgeTrace) -> tuple[str, tuple[str, ...]]:
    return trace.outcome.mode, tuple(candidate.knowledge_key for candidate in trace.outcome.candidates)


def aggregate_repetitions(traces: tuple[JudgeTrace, ...]) -> RepetitionResult:
    if len(traces) != 3:
        raise ValueError("exactly three judge traces are required")
    groups: dict[tuple[str, tuple[str, ...]], list[JudgeTrace]] = {}
    for trace in traces:
        groups.setdefault(_signature(trace), []).append(trace)
    majority_group = max(groups.values(), key=lambda group: len(group))
    majority = majority_group[0]
    mode_rank = {"none": 0, "advisory": 1, "exact": 2}
    worst = min(traces, key=lambda trace: (mode_rank[trace.outcome.mode], len(trace.outcome.candidates)))
    stable = (
        all(trace.error is None for trace in traces)
        and len(groups) == 1
    )
    return RepetitionResult(
        majority=majority,
        worst=worst,
        stable=stable,
        agreement_count=len(majority_group),
        input_tokens=sum(trace.input_tokens for trace in traces),
        output_tokens=sum(trace.output_tokens for trace in traces),
        request_count=sum(1 for trace in traces if trace.parse_outcome != "bypassed"),
    )
