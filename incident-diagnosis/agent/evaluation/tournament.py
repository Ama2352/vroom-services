"""Calibration-first, split-safe retrieval reranker tournament."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from time import perf_counter
from typing import Mapping

from evaluation.baseline import rank_current_coverage, seed_store
from evaluation.bm25_variants import generate_bm25_candidates
from evaluation.fixture_loader import load_cases, validate_tournament_catalog
from evaluation.llm_judge import JudgeTrace, build_prompt, parse_judgment
from evaluation.model_artifacts import (
    ensure_model_artifact,
    load_manifest,
    verify_sha256,
)
from evaluation.models import RankedCandidate, RetrievalCase, RetrievalOutcome, VariantConfig
from evaluation.resource_probe import measure_local_adapter, nearest_rank_percentile
from evaluation.tournament_metrics import (
    candidate_recall_at_8,
    passes_tournament_gate,
    select_recommendation,
    select_score_floor,
    summarize_tournament,
)
from evaluation.tournament_models import (
    DecisionTrace,
    OperationalMetrics,
    SystemEvaluation,
)
from evaluation.tournament_reporting import write_reports


_AGENT_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_FIXTURES = Path(__file__).with_name("fixtures") / "retrieval_cases_v2.json"
_DEFAULT_REPORT_DIR = Path(__file__).with_name("reports")
_DEFAULT_MODEL_CACHE = Path(__file__).with_name(".models")
_PROMPT_PATH = Path(__file__).with_name("prompts") / "retrieval_judge_v1.txt"
_CANDIDATE_CONFIG = VariantConfig("rich_joined", "rich", "joined", 0.0)
_PACKAGE_NAMES = (
    "fakeredis", "flask", "huggingface-hub", "numpy", "onnxruntime",
    "psutil", "rank-bm25", "redis", "requests", "transformers",
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _metric_dict(summary) -> dict:
    return {
        **asdict(summary),
        "top1_accuracy": summary.top1_accuracy,
        "recall_at_3": summary.recall_at_3,
        "false_positive_rate": summary.false_positive_rate,
        "mean_reciprocal_rank": summary.mean_reciprocal_rank,
        "abstention_accuracy": summary.abstention_accuracy,
    }


def _outcome_signature(outcome: RetrievalOutcome) -> tuple:
    return (
        outcome.mode,
        outcome.exact_ambiguous,
        tuple(candidate.knowledge_key for candidate in outcome.candidates),
    )


def _as_trace(value) -> DecisionTrace | JudgeTrace:
    if isinstance(value, (DecisionTrace, JudgeTrace)):
        return value
    if isinstance(value, RetrievalOutcome):
        return DecisionTrace(value)
    if hasattr(value, "outcome"):
        return value
    raise TypeError("adapter must return a decision trace or retrieval outcome")


def _invoke_local(adapter, batch, floor: float):
    if hasattr(adapter, "evaluate"):
        return _as_trace(adapter.evaluate(batch, floor=floor))
    if hasattr(adapter, "rerank"):
        return _as_trace(adapter.rerank(batch, floor=floor))
    if callable(adapter):
        return _as_trace(adapter(batch, floor=floor))
    raise TypeError("local adapter must be callable or expose evaluate/rerank")


def _invoke_llm(adapter, batch, prompt_template: str | None = None):
    if hasattr(adapter, "evaluate"):
        return _as_trace(adapter.evaluate(batch))
    if callable(adapter) and not hasattr(adapter, "judge"):
        return _as_trace(adapter(batch))
    if not hasattr(adapter, "judge"):
        raise TypeError("LLM adapter must be callable or expose evaluate/judge")
    if batch[1].mode == "exact" or not batch[1].candidates:
        return JudgeTrace(batch[1], parse_outcome="bypassed")

    started = perf_counter()
    metadata_values: dict = {}
    try:
        if prompt_template is None:
            prompt_template = _PROMPT_PATH.read_text(encoding="utf-8").rstrip("\n")
        prompt = build_prompt(batch, instructions=prompt_template)
        raw, metadata_values = adapter.judge(prompt)
        prompt_sha256 = _sha256_bytes(prompt.encode("utf-8"))
        try:
            parsed = parse_judgment(raw, batch, prompt_sha256=prompt_sha256)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            return JudgeTrace(
                RetrievalOutcome("none", (), batch[1].exact_ambiguous),
                latency_ms=float(metadata_values.get("latency_ms", (perf_counter() - started) * 1000)),
                error=f"{type(exc).__name__}: {exc}",
                input_tokens=int(metadata_values.get("prompt_tokens", 0) or 0),
                output_tokens=int(metadata_values.get("completion_tokens", 0) or 0),
                http_status=metadata_values.get("http_status"),
                parse_outcome="malformed",
                prompt_sha256=prompt_sha256,
            )
        return JudgeTrace(
            parsed.outcome,
            parsed.decisions,
            float(metadata_values.get("latency_ms", (perf_counter() - started) * 1000)),
            None,
            int(metadata_values.get("prompt_tokens", 0) or 0),
            int(metadata_values.get("completion_tokens", 0) or 0),
            metadata_values.get("http_status"),
            "parsed",
            parsed.prompt_sha256,
        )
    except Exception as exc:
        if hasattr(exc, "metadata"):
            metadata_values = exc.metadata
        return JudgeTrace(
            RetrievalOutcome("none", (), batch[1].exact_ambiguous),
            latency_ms=float(
                metadata_values.get("latency_ms", (perf_counter() - started) * 1000)
            ),
            error=f"{type(exc).__name__}: {exc}",
            input_tokens=int(metadata_values.get("prompt_tokens", 0) or 0),
            output_tokens=int(metadata_values.get("completion_tokens", 0) or 0),
            http_status=metadata_values.get("http_status"),
            parse_outcome="provider_error",
        )


def _freeze_adapter(adapter, name: str, value) -> None:
    freeze = getattr(adapter, "freeze", None)
    if freeze is None:
        return
    try:
        freeze(name, value)
    except TypeError:
        freeze(value)


def _apply_floor(
    raw: Mapping[str, RetrievalOutcome], floor: float, *, limit: int = 3
) -> dict[str, RetrievalOutcome]:
    result = {}
    for case_id, outcome in raw.items():
        if outcome.mode == "exact":
            result[case_id] = outcome
            continue
        candidates = tuple(
            candidate for candidate in outcome.candidates if candidate.score >= floor
        )[:limit]
        result[case_id] = RetrievalOutcome(
            "advisory" if candidates else "none", candidates, outcome.exact_ambiguous
        )
    return result


def _scored_calibration_outcome(
    candidate_outcome: RetrievalOutcome, trace
) -> RetrievalOutcome:
    if candidate_outcome.mode == "exact":
        return candidate_outcome
    scores = {
        decision.knowledge_key: decision.score
        for decision in getattr(trace, "decisions", ())
        if decision.score is not None
    }
    ordered = trace.outcome.candidates
    if not scores:
        return trace.outcome
    rescored = tuple(
        replace(candidate, score=float(scores[candidate.knowledge_key]))
        for candidate in ordered
        if candidate.knowledge_key in scores
    )
    return RetrievalOutcome(
        "advisory" if rescored else "none", rescored, candidate_outcome.exact_ambiguous
    )


def _operational_from_adapter(adapter) -> OperationalMetrics:
    value = getattr(adapter, "operational", None)
    if isinstance(value, OperationalMetrics):
        return value
    if isinstance(value, dict):
        allowed = OperationalMetrics.__dataclass_fields__
        return OperationalMetrics(**{key: item for key, item in value.items() if key in allowed})
    return OperationalMetrics()


def _operational_from_measurement(measurement: dict) -> OperationalMetrics:
    return OperationalMetrics(
        artifact_mb=measurement.get("artifact_mb"),
        estimated_container_delta_mb=measurement.get("estimated_container_delta_mb"),
        cold_load_ms=measurement.get("cold_load_ms"),
        p50_ms=measurement.get("p50_ms"),
        p95_ms=measurement.get("p95_ms"),
        peak_rss_mb=measurement.get("peak_rss_mb"),
    )


def _system_payload(system: SystemEvaluation, *, status="available", error=None, **extra):
    result = {
        "name": system.name,
        "kind": system.kind,
        "status": status,
        "error": error,
        "calibration": _metric_dict(system.calibration),
        "held_out": _metric_dict(system.held_out),
        "threshold": system.threshold,
        "stable": system.stable,
        "passed": system.passed,
        "operational": asdict(system.operational),
        "failure_reasons": list(system.failure_reasons),
    }
    result.update(extra)
    return result


def _unavailable_system(name: str, kind: str, baseline_cal, baseline_held, error) -> dict:
    message = error if isinstance(error, str) else error.get("message", str(error))
    structured = (
        {"type": "Unavailable", "message": error}
        if isinstance(error, str)
        else error
    )
    return {
        "name": name,
        "kind": kind,
        "status": "unavailable",
        "error": structured,
        "calibration": None,
        "held_out": None,
        "threshold": None,
        "stable": False,
        "passed": False,
        "operational": asdict(OperationalMetrics()),
        "failure_reasons": [message],
    }


def _failed_reasons(candidate, baseline, stable, operational, kind) -> tuple[str, ...]:
    reasons = []
    if candidate.forbidden_acceptances:
        reasons.append("forbidden candidate accepted")
    if candidate.exact_failures:
        reasons.append("exact-mode contract failed")
    if candidate.false_positive_rate > baseline.false_positive_rate:
        reasons.append("false-positive rate exceeded baseline")
    if candidate.top1_accuracy < baseline.top1_accuracy:
        reasons.append("top-1 accuracy regressed")
    if candidate.recall_at_3 < baseline.recall_at_3:
        reasons.append("recall@3 regressed")
    if not stable:
        reasons.append("ordered outcomes were unstable")
    if kind == "local":
        if operational.peak_rss_mb is None:
            reasons.append("peak RSS unavailable")
        elif operational.peak_rss_mb > 500.0:
            reasons.append("peak RSS exceeded 500 MB")
        if operational.p95_ms is None:
            reasons.append("p95 latency unavailable")
        elif operational.p95_ms > 1000.0:
            reasons.append("p95 latency exceeded 1000 ms")
    return tuple(reasons)


def _git_commit() -> str | None:
    root = Path(__file__).resolve().parents[3]
    try:
        completed = subprocess.run(
            [
                "git", "-c", f"safe.directory={root.as_posix()}",
                "rev-parse", "HEAD",
            ],
            cwd=root, capture_output=True, text=True, check=True, timeout=5,
        )
        return completed.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _total_ram_mb() -> float | None:
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 * 1024)
    except (ImportError, OSError):
        return None


def _environment(cases, fixture_bytes: bytes) -> dict:
    packages = {}
    for package in _PACKAGE_NAMES:
        try:
            packages[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            packages[package] = None
    specs = load_manifest()
    prompt_text = _PROMPT_PATH.read_text(encoding="utf-8")
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "package_versions": packages,
        "os": platform.platform(),
        "cpu_model": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER") or "unknown",
        "logical_core_count": os.cpu_count(),
        "total_ram_mb": _total_ram_mb(),
        "git_commit": _git_commit(),
        "fixture_sha256": _sha256_bytes(fixture_bytes),
        "fixture_case_count": len(cases),
        "model_hashes": {
            spec.name: {
                "repo_id": spec.repo_id,
                "revision": spec.revision,
                "artifact_sha256": spec.sha256,
            }
            for spec in specs
        },
        "prompt": {
            "provider": "groq",
            "model_id": "llama-3.1-8b-instant",
            "text": prompt_text,
            "sha256": _sha256_bytes(prompt_text.encode("utf-8")),
        },
    }


def _fixture_input(cases_or_path) -> tuple[tuple[RetrievalCase, ...], bytes]:
    if isinstance(cases_or_path, (str, Path)):
        path = Path(cases_or_path)
        return load_cases(path), path.read_bytes()
    cases = tuple(cases_or_path)
    canonical = json.dumps(
        [asdict(case) if is_dataclass(case) else case for case in cases],
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return cases, canonical


def _validate_cases(cases: tuple[RetrievalCase, ...]) -> tuple[tuple, tuple]:
    validate_tournament_catalog(cases)
    calibration = tuple(case for case in cases if case.split == "calibration")
    held_out = tuple(case for case in cases if case.split == "held_out")
    all_ids = tuple(case.id for case in cases)
    split_ids = tuple(case.id for case in (*calibration, *held_out))
    assert len(all_ids) == len(set(all_ids)), "every case ID must be unique"
    assert Counter(all_ids) == Counter(split_ids), (
        "every case ID must appear exactly once in calibration or held_out"
    )
    return calibration, held_out


def _empty_incomplete(exc: BaseException) -> dict:
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision": "INCOMPLETE",
        "recommendation": None,
        "environment": {},
        "dataset": {},
        "shared_candidates": {},
        "systems": {},
        "llm_repetitions": {},
        "informative_failures": [],
        "failure_reasons": [{
            "phase": "fixture_validation",
            "type": type(exc).__name__,
            "message": str(exc),
        }],
        "reproduction": {},
        "debug": {"candidate_ids_by_system": {}},
    }


def _calibrate_injected_local(name, adapter, calibration, batches, baseline_cal):
    calibration_traces = {
        case.id: _invoke_local(adapter, batches[case.id], float("-inf"))
        for case in calibration
    }
    unthresholded = {
        case.id: _scored_calibration_outcome(
            batches[case.id][1], calibration_traces[case.id]
        )
        for case in calibration
    }
    floor = select_score_floor(calibration, unthresholded, baseline_cal)
    frozen_floor = 0.0 if floor is None else floor
    _freeze_adapter(adapter, name, frozen_floor)
    calibration_outcomes = _apply_floor(unthresholded, frozen_floor)
    return {
        "name": name,
        "adapter": adapter,
        "floor": floor,
        "frozen_floor": frozen_floor,
        "calibration_metrics": summarize_tournament(calibration, calibration_outcomes),
        "calibration_traces": calibration_traces,
        "operational": _operational_from_adapter(adapter),
    }


def _held_injected_local(state, held_out, batches, baseline_held):
    name = state["name"]
    adapter = state["adapter"]
    frozen_floor = state["frozen_floor"]
    runs = []
    trace_runs = []
    traces = []
    for _ in range(2):
        run = {}
        trace_run = {}
        for case in held_out:
            trace = _invoke_local(adapter, batches[case.id], frozen_floor)
            traces.append(trace)
            trace_run[case.id] = trace
            run[case.id] = trace.outcome
        runs.append(run)
        trace_runs.append(trace_run)
    stable = all(
        _outcome_signature(runs[0][case.id]) == _outcome_signature(runs[1][case.id])
        for case in held_out
    ) and all(getattr(trace, "error", None) is None for trace in traces)
    held_metrics = summarize_tournament(held_out, runs[0])
    operational = state["operational"]
    passed = state["floor"] is not None and passes_tournament_gate(
        held_metrics, baseline_held, stable=stable,
        operational=operational, system_kind="local",
    )
    reasons = list(_failed_reasons(
        held_metrics, baseline_held, stable, operational, "local"
    ))
    if state["floor"] is None:
        reasons.insert(0, "no calibration score floor passed")
    evaluation = SystemEvaluation(
        name, "local", state["calibration_metrics"], held_metrics,
        state["floor"], stable,
        passed, operational, tuple(dict.fromkeys(reasons)),
    )
    payload = _system_payload(
        evaluation,
        calibration_traces={
            case_id: _trace_json(trace)
            for case_id, trace in state["calibration_traces"].items()
        },
        held_out_runs=[
            {case_id: _trace_json(trace) for case_id, trace in run.items()}
            for run in trace_runs
        ],
    )
    return payload, evaluation, runs[0]


def _resolve_artifact(spec, cache_root: Path, prepare_models: bool) -> tuple[Path, Path]:
    model_dir = Path(cache_root) / spec.name
    if prepare_models:
        artifact = ensure_model_artifact(spec, model_dir)
    else:
        artifact = model_dir / spec.onnx_file
        if not artifact.is_file():
            raise FileNotFoundError(
                f"model artifact missing at {artifact}; use --prepare-models to obtain it"
            )
        artifact = verify_sha256(artifact, spec.sha256).resolve()
    return model_dir, artifact


def _calibrate_measured_local(
    name, spec, cache_root, prepare_models, calibration, batches,
    baseline_cal, resource_measure,
):
    model_dir, artifact = _resolve_artifact(spec, cache_root, prepare_models)
    calibration_measurement = resource_measure(
        name=name, artifact_path=artifact, model_dir=model_dir, spec=spec,
        batches=tuple(batches[case.id] for case in calibration),
        floor=float("-inf"), repetitions=1,
    )
    if not calibration_measurement.get("available"):
        message = calibration_measurement.get("error", {}).get(
            "message", "calibration worker failed"
        )
        raise RuntimeError(message)
    calibration_traces = calibration_measurement["runs"][0]
    unthresholded = {
        case.id: _scored_calibration_outcome(
            batches[case.id][1], _as_trace(calibration_traces[case.id])
        )
        for case in calibration
    }
    floor = select_score_floor(calibration, unthresholded, baseline_cal)
    frozen_floor = 0.0 if floor is None else floor
    calibration_outcomes = _apply_floor(unthresholded, frozen_floor)
    return {
        "name": name,
        "spec": spec,
        "model_dir": model_dir,
        "artifact": artifact,
        "floor": floor,
        "frozen_floor": frozen_floor,
        "calibration_metrics": summarize_tournament(calibration, calibration_outcomes),
        "calibration_traces": {
            case_id: _as_trace(trace)
            for case_id, trace in calibration_traces.items()
        },
        "resource_measure": resource_measure,
    }


def _held_measured_local(state, held_out, batches, baseline_held):
    name = state["name"]
    resource_measure = state["resource_measure"]
    held_measurement = resource_measure(
        name=name, artifact_path=state["artifact"], model_dir=state["model_dir"],
        spec=state["spec"],
        batches=tuple(batches[case.id] for case in held_out),
        floor=state["frozen_floor"], repetitions=2,
    )
    if not held_measurement.get("available"):
        message = held_measurement.get("error", {}).get(
            "message", "held-out worker failed"
        )
        raise RuntimeError(message)
    trace_runs = [
        {case_id: _as_trace(trace) for case_id, trace in run.items()}
        for run in held_measurement["runs"]
    ]
    runs = [
        {case_id: trace.outcome for case_id, trace in run.items()}
        for run in trace_runs
    ]
    stable = all(
        _outcome_signature(runs[0][case.id]) == _outcome_signature(runs[1][case.id])
        for case in held_out
    )
    held_metrics = summarize_tournament(held_out, runs[0])
    operational = _operational_from_measurement(held_measurement)
    passed = state["floor"] is not None and passes_tournament_gate(
        held_metrics, baseline_held, stable=stable,
        operational=operational, system_kind="local",
    )
    reasons = list(_failed_reasons(held_metrics, baseline_held, stable, operational, "local"))
    if state["floor"] is None:
        reasons.insert(0, "no calibration score floor passed")
    evaluation = SystemEvaluation(
        name, "local", state["calibration_metrics"], held_metrics,
        state["floor"], stable,
        passed, operational, tuple(dict.fromkeys(reasons)),
    )
    payload = _system_payload(
        evaluation,
        calibration_traces={
            case_id: _trace_json(trace)
            for case_id, trace in state["calibration_traces"].items()
        },
        held_out_runs=[
            {case_id: _trace_json(trace) for case_id, trace in run.items()}
            for run in trace_runs
        ],
        resource_measurement={
            key: value for key, value in held_measurement.items() if key != "runs"
        },
    )
    return payload, evaluation, runs[0]


def _calibrate_llm(adapter, calibration, batches, prompt_revision_hook):
    preflight = None
    if hasattr(adapter, "preflight"):
        preflight = adapter.preflight()
        if getattr(preflight, "error", None):
            raise RuntimeError(f"LLM preflight failed: {preflight.error}")

    calibration_prompt = _PROMPT_PATH.read_text(encoding="utf-8").rstrip("\n")
    calibration_traces = {
        case.id: _invoke_llm(adapter, batches[case.id], calibration_prompt)
        for case in calibration
    }
    calibration_outcomes = {
        case_id: trace.outcome for case_id, trace in calibration_traces.items()
    }
    revision = (
        prompt_revision_hook(calibration, calibration_traces)
        if prompt_revision_hook is not None else None
    )
    if revision is None:
        active_prompt = calibration_prompt
    elif not isinstance(revision, str):
        raise TypeError("prompt revision hook must return a string or None")
    elif not revision:
        raise ValueError("prompt revision hook returned an empty prompt")
    elif not hasattr(adapter, "judge"):
        raise ValueError(
            "prompt revision requires an adapter with an explicit judge prompt boundary"
        )
    else:
        active_prompt = revision
    prompt_hash = _sha256_bytes(active_prompt.encode("utf-8"))
    frozen_identity = {
        "prompt_sha256": prompt_hash,
        "prompt_text": active_prompt,
        "provider": getattr(adapter, "provider", "groq"),
        "model_id": getattr(adapter, "model", "llama-3.1-8b-instant"),
    }
    _freeze_adapter(adapter, "llm", frozen_identity)
    return {
        "adapter": adapter,
        "preflight": preflight,
        "calibration_traces": calibration_traces,
        "calibration_metrics": summarize_tournament(calibration, calibration_outcomes),
        "frozen_identity": frozen_identity,
        "active_prompt": active_prompt,
    }


def _held_llm(state, held_out, batches, baseline_held, input_price, output_price):
    adapter = state["adapter"]
    run_outcomes = [dict(), dict(), dict()]
    by_case = {}
    all_traces = list(state["calibration_traces"].values())
    for case in held_out:
        case_traces = tuple(
            _invoke_llm(adapter, batches[case.id], state["active_prompt"])
            for _ in range(3)
        )
        all_traces.extend(case_traces)
        signatures = [_outcome_signature(trace.outcome) for trace in case_traces]
        majority_signature, agreement = Counter(signatures).most_common(1)[0]
        majority_trace = next(
            trace for trace, signature in zip(case_traces, signatures)
            if signature == majority_signature
        )
        for index, trace in enumerate(case_traces):
            run_outcomes[index][case.id] = trace.outcome
        by_case[case.id] = {
            "agreement_count": agreement,
            "stable": agreement == 3 and all(trace.error is None for trace in case_traces),
            "majority": majority_trace.outcome,
            "traces": case_traces,
        }

    majority_outcomes = {
        case.id: by_case[case.id]["majority"] for case in held_out
    }
    majority_metrics = summarize_tournament(held_out, majority_outcomes)
    run_metrics = tuple(summarize_tournament(held_out, run) for run in run_outcomes)
    worst_index, worst_metrics = max(enumerate(run_metrics), key=lambda item: (
        item[1].forbidden_acceptances,
        item[1].exact_failures,
        item[1].false_positive_rate,
        -item[1].top1_accuracy,
        -item[1].recall_at_3,
        item[0],
    ))
    stable = all(value["stable"] for value in by_case.values())
    request_traces = [
        trace for trace in all_traces
        if getattr(trace, "parse_outcome", "attempted") != "bypassed"
    ]
    preflight = state["preflight"]
    preflight_count = int(preflight is not None)
    preflight_input = int(getattr(preflight, "input_tokens", 0) or 0)
    preflight_output = int(getattr(preflight, "output_tokens", 0) or 0)
    preflight_latencies = (
        (float(getattr(preflight, "latency_ms", 0.0)),)
        if preflight is not None else ()
    )
    request_count = len(request_traces) + preflight_count
    input_tokens = (
        preflight_input
        + sum(int(getattr(trace, "input_tokens", 0)) for trace in all_traces)
    )
    output_tokens = (
        preflight_output
        + sum(int(getattr(trace, "output_tokens", 0)) for trace in all_traces)
    )
    latencies = preflight_latencies + tuple(
        float(getattr(trace, "latency_ms", 0.0)) for trace in request_traces
    )
    provider_failures = sum(
        getattr(trace, "parse_outcome", "") == "provider_error"
        for trace in all_traces
    )
    operational = OperationalMetrics(
        p50_ms=nearest_rank_percentile(latencies, 0.50) if latencies else 0.0,
        p95_ms=nearest_rank_percentile(latencies, 0.95) if latencies else 0.0,
        request_count=request_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        current_spend_usd=0.0,
        theoretical_spend_usd=(
            input_tokens * input_price / 1_000_000
            + output_tokens * output_price / 1_000_000
        ),
    )
    passed = passes_tournament_gate(
        worst_metrics, baseline_held, stable=stable,
        operational=operational, system_kind="llm",
    )
    reasons = _failed_reasons(worst_metrics, baseline_held, stable, operational, "llm")
    evaluation = SystemEvaluation(
        "llm", "llm", state["calibration_metrics"], worst_metrics, None, stable,
        passed, operational, reasons,
    )
    payload = _system_payload(
        evaluation,
        majority_held_out=_metric_dict(majority_metrics),
        worst_held_out=_metric_dict(worst_metrics),
        worst_run_index=worst_index,
        provider_failures=provider_failures,
        frozen_identity=state["frozen_identity"],
        preflight=(
            asdict(state["preflight"])
            if is_dataclass(state["preflight"])
            else state["preflight"]
        ),
    )
    repetitions = {
        case_id: {
            "agreement_count": value["agreement_count"],
            "stable": value["stable"],
            "majority": _outcome_json(value["majority"]),
            "runs": [_trace_json(trace) for trace in value["traces"]],
        }
        for case_id, value in by_case.items()
    }
    return payload, evaluation, repetitions, majority_outcomes


def _candidate_json(candidate: RankedCandidate) -> dict:
    return {
        "knowledge_key": candidate.knowledge_key,
        "score": candidate.score,
        "source": candidate.source,
        "source_id": candidate.source_id,
        "matched_terms": list(candidate.matched_terms),
        "root_cause_pattern": candidate.root_cause_pattern,
        "fix_action": candidate.fix_action,
        "context_notes": candidate.context_notes,
        "document_text": candidate.document_text,
    }


def _outcome_json(outcome: RetrievalOutcome) -> dict:
    return {
        "mode": outcome.mode,
        "exact_ambiguous": outcome.exact_ambiguous,
        "candidates": [_candidate_json(candidate) for candidate in outcome.candidates],
    }


def _trace_json(trace) -> dict:
    return {
        "outcome": _outcome_json(trace.outcome),
        "decisions": [
            {
                "knowledge_key": decision.knowledge_key,
                "accepted": decision.accepted,
                "score": decision.score,
                "grade": decision.grade,
                "supporting_fields": list(decision.supporting_fields),
                "conflicting_fields": list(decision.conflicting_fields),
                "reason": decision.reason,
            }
            for decision in getattr(trace, "decisions", ())
        ],
        "latency_ms": float(getattr(trace, "latency_ms", 0.0)),
        "error": getattr(trace, "error", None),
        "input_tokens": int(getattr(trace, "input_tokens", 0)),
        "output_tokens": int(getattr(trace, "output_tokens", 0)),
        "http_status": getattr(trace, "http_status", None),
        "parse_outcome": getattr(trace, "parse_outcome", "not_recorded"),
        "prompt_sha256": getattr(trace, "prompt_sha256", ""),
    }


def _informative_failures(cases, outcomes_by_system, llm_repetitions) -> list[dict]:
    """Serialize concise, actual per-case failures for report selection."""
    records = []
    for case in cases:
        for system, outcomes in outcomes_by_system.items():
            outcome = outcomes.get(case.id)
            if outcome is None:
                continue
            keys = [candidate.knowledge_key for candidate in outcome.candidates]
            forbidden = sorted(set(keys).intersection(case.forbidden_keys))
            if forbidden:
                records.append({
                    "case_id": case.id,
                    "system": system,
                    "failure_type": "forbidden_acceptance",
                    "expected_mode": case.expected_mode,
                    "observed_mode": outcome.mode,
                    "selected_keys": keys,
                    "reason": f"accepted forbidden key(s): {', '.join(forbidden)}",
                })
            if case.id == "dns_no_match" and system == "baseline":
                records.append({
                    "case_id": case.id,
                    "system": system,
                    "failure_type": "hard_negative",
                    "expected_mode": "none",
                    "observed_mode": outcome.mode,
                    "selected_keys": keys,
                    "reason": (
                        f"expected abstention; {system} returned {outcome.mode} "
                        f"with {len(keys)} accepted candidate(s)"
                    ),
                })
            if case.expected_mode == "none" and keys and not forbidden:
                records.append({
                    "case_id": case.id,
                    "system": system,
                    "failure_type": "false_positive",
                    "expected_mode": "none",
                    "observed_mode": outcome.mode,
                    "selected_keys": keys,
                    "reason": f"accepted unsupported key(s): {', '.join(keys)}",
                })
            elif case.expected_mode != "none" and not set(keys).intersection(case.expected_keys):
                records.append({
                    "case_id": case.id,
                    "system": system,
                    "failure_type": "missed_positive",
                    "expected_mode": case.expected_mode,
                    "observed_mode": outcome.mode,
                    "selected_keys": keys,
                    "reason": "did not return an expected diagnosis key",
                })
    for case_id, repetition in llm_repetitions.items():
        if not repetition.get("stable", True):
            records.append({
                "case_id": case_id,
                "system": "llm",
                "failure_type": "unstable_llm",
                "reason": "three held-out LLM runs did not agree cleanly",
            })
    return records


def run_tournament(
    cases_or_path=_DEFAULT_FIXTURES,
    *,
    adapters: Mapping[str, object] | None = None,
    include_llm: bool = False,
    model_cache: Path | None = None,
    report_dir: Path | None = None,
    prepare_models: bool = False,
    llm_input_usd_per_million: float = 0.0,
    llm_output_usd_per_million: float = 0.0,
    pricing_source_url: str | None = None,
    pricing_retrieved_at: str | None = None,
    resource_measure=measure_local_adapter,
    prompt_revision_hook=None,
) -> dict:
    """Run the tournament without writing reports or changing production."""
    try:
        cases, fixture_bytes = _fixture_input(cases_or_path)
        calibration, held_out = _validate_cases(cases)
    except (AssertionError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _empty_incomplete(exc)

    adapters = dict(adapters or {})
    rdb = seed_store()
    baseline_all = {case.id: rank_current_coverage(rdb, case) for case in cases}
    baseline_cal = summarize_tournament(
        calibration, {case.id: baseline_all[case.id] for case in calibration}
    )
    baseline_held = summarize_tournament(
        held_out, {case.id: baseline_all[case.id] for case in held_out}
    )

    shared = {
        case.id: generate_bm25_candidates(rdb, case, _CANDIDATE_CONFIG, limit=8)
        for case in cases
    }
    batches = {case.id: (case, shared[case.id]) for case in cases}
    candidate_ids = {
        case.id: tuple(candidate.knowledge_key for candidate in shared[case.id].candidates)
        for case in cases
    }
    debug_systems = ["bm25", "minilm", "mixedbread_xsmall"]
    if include_llm:
        debug_systems.append("llm")

    baseline_eval = SystemEvaluation(
        "baseline", "baseline", baseline_cal, baseline_held, None, True, True
    )
    systems = {"baseline": _system_payload(baseline_eval)}
    outcomes_by_system = {"baseline": baseline_all}
    evaluations = []
    failures = []

    raw_cal = {case.id: shared[case.id] for case in calibration}
    bm25_floor = select_score_floor(calibration, raw_cal, baseline_cal)
    effective_bm25_floor = 0.0 if bm25_floor is None else bm25_floor
    bm25_cal_outcomes = _apply_floor(raw_cal, effective_bm25_floor)
    bm25_cal_metrics = summarize_tournament(calibration, bm25_cal_outcomes)

    # Calibrate and freeze every challenger before any held-out decision call.
    specs = {spec.name: spec for spec in load_manifest()}
    cache_root = Path(model_cache or _DEFAULT_MODEL_CACHE)
    local_states = {}
    for name in ("minilm", "mixedbread_xsmall"):
        try:
            if name in adapters:
                local_states[name] = _calibrate_injected_local(
                    name, adapters[name], calibration, batches, baseline_cal,
                )
            else:
                local_states[name] = _calibrate_measured_local(
                    name, specs[name], cache_root, prepare_models,
                    calibration, batches, baseline_cal, resource_measure,
                )
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            systems[name] = _unavailable_system(
                name, "local", baseline_cal, baseline_held, error
            )
            failures.append({"system": name, "phase": "local_calibration", **error})

    llm_repetitions = {}
    llm_state = None
    if include_llm:
        llm_adapter = adapters.get("llm")
        if llm_adapter is None:
            api_key = os.environ.get("GROQ_KEY")
            if not api_key:
                message = "GROQ_KEY is required when --include-llm is enabled"
                systems["llm"] = _unavailable_system(
                    "llm", "llm", baseline_cal, baseline_held, message
                )
                failures.append({
                    "system": "llm", "phase": "configuration",
                    "type": "MissingEnvironmentVariable", "message": message,
                })
            else:
                from evaluation.llm_judge import GroqJudgeClient
                llm_adapter = GroqJudgeClient(api_key)
        if llm_adapter is not None:
            try:
                llm_state = _calibrate_llm(
                    llm_adapter, calibration, batches, prompt_revision_hook,
                )
            except BaseException as exc:
                error = {"type": type(exc).__name__, "message": str(exc)}
                systems["llm"] = _unavailable_system(
                    "llm", "llm", baseline_cal, baseline_held, error
                )
                failures.append({"system": "llm", "phase": "llm_calibration", **error})
    else:
        systems["llm"] = _unavailable_system(
            "llm", "llm", baseline_cal, baseline_held,
            "LLM challenger was not enabled",
        )

    # Held-out phase begins only after all available configurations are frozen.
    raw_held = {case.id: shared[case.id] for case in held_out}
    bm25_held_outcomes = _apply_floor(raw_held, effective_bm25_floor)
    bm25_held_metrics = summarize_tournament(held_out, bm25_held_outcomes)
    bm25_passed = bm25_floor is not None and passes_tournament_gate(
        bm25_held_metrics, baseline_held, stable=True,
        operational=OperationalMetrics(), system_kind="bm25",
    )
    bm25_reasons = list(_failed_reasons(
        bm25_held_metrics, baseline_held, True, OperationalMetrics(), "bm25"
    ))
    if bm25_floor is None:
        bm25_reasons.insert(0, "no calibration score floor passed")
    bm25_eval = SystemEvaluation(
        "bm25", "bm25", bm25_cal_metrics, bm25_held_metrics,
        bm25_floor, True, bm25_passed, OperationalMetrics(),
        tuple(dict.fromkeys(bm25_reasons)),
    )
    systems["bm25"] = _system_payload(
        bm25_eval,
        candidate_recall_at_8={
            "calibration": candidate_recall_at_8(calibration, raw_cal),
            "held_out": candidate_recall_at_8(held_out, raw_held),
        },
    )
    outcomes_by_system["bm25"] = {**bm25_cal_outcomes, **bm25_held_outcomes}

    for name in ("minilm", "mixedbread_xsmall"):
        state = local_states.get(name)
        if state is None:
            continue
        try:
            if "adapter" in state:
                payload, evaluation, held_outcomes = _held_injected_local(
                    state, held_out, batches, baseline_held,
                )
            else:
                payload, evaluation, held_outcomes = _held_measured_local(
                    state, held_out, batches, baseline_held,
                )
            systems[name] = payload
            outcomes_by_system[name] = held_outcomes
            evaluations.append(evaluation)
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            systems[name] = _unavailable_system(
                name, "local", baseline_cal, baseline_held, error
            )
            failures.append({"system": name, "phase": "local_held_out", **error})

    if llm_state is not None:
        try:
            payload, evaluation, llm_repetitions, held_outcomes = _held_llm(
                llm_state, held_out, batches, baseline_held,
                llm_input_usd_per_million, llm_output_usd_per_million,
            )
            systems["llm"] = payload
            outcomes_by_system["llm"] = {
                **{
                    case_id: trace.outcome
                    for case_id, trace in llm_state["calibration_traces"].items()
                },
                **held_outcomes,
            }
            evaluations.append(evaluation)
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            systems["llm"] = _unavailable_system(
                "llm", "llm", baseline_cal, baseline_held, error
            )
            failures.append({"system": "llm", "phase": "llm_held_out", **error})

    passing_locals = [evaluation for evaluation in evaluations if evaluation.kind == "local"]
    recommendation = select_recommendation(passing_locals)
    if recommendation is not None:
        decision = "LOCAL_PASS"
    else:
        passing_llm = [evaluation for evaluation in evaluations if evaluation.kind == "llm"]
        recommendation = select_recommendation(passing_llm)
        decision = "LLM_ONLY_PASS" if recommendation is not None else "FAIL"

    dataset = {
        "case_count": len(cases),
        "calibration_count": len(calibration),
        "held_out_count": len(held_out),
        "held_out_no_match_cases": sum(
            case.expected_mode == "none" for case in held_out
        ),
        "positive_count": sum(case.expected_mode != "none" for case in cases),
        "no_match_count": sum(case.expected_mode == "none" for case in cases),
        "case_ids": {split: [case.id for case in cases if case.split == split]
                     for split in ("calibration", "held_out")},
    }
    environment = _environment(cases, fixture_bytes)
    if pricing_source_url or pricing_retrieved_at:
        environment["pricing_snapshot"] = {
            "source_url": pricing_source_url,
            "retrieved_at": pricing_retrieved_at,
            "provider": "groq",
            "model_id": "llama-3.1-8b-instant",
            "input_usd_per_million": float(llm_input_usd_per_million),
            "output_usd_per_million": float(llm_output_usd_per_million),
        }
    if llm_state is not None:
        frozen = llm_state["frozen_identity"]
        environment["prompt"] = {
            "provider": frozen["provider"],
            "model_id": frozen["model_id"],
            "text": frozen["prompt_text"],
            "sha256": frozen["prompt_sha256"],
        }
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "recommendation": (
            {"name": recommendation.name, "kind": recommendation.kind}
            if recommendation else None
        ),
        "environment": environment,
        "dataset": dataset,
        "shared_candidates": {
            case_id: _outcome_json(outcome) for case_id, outcome in shared.items()
        },
        "systems": systems,
        "llm_repetitions": llm_repetitions,
        "informative_failures": _informative_failures(
            cases, outcomes_by_system, llm_repetitions
        ),
        "failure_reasons": failures,
        "reproduction": {
            "command": _reproduction_command(
                cases_or_path=cases_or_path,
                report_dir=report_dir or _DEFAULT_REPORT_DIR,
                model_cache=cache_root,
                prepare_models=prepare_models,
                include_llm=include_llm,
                input_price=llm_input_usd_per_million,
                output_price=llm_output_usd_per_million,
                pricing_source_url=pricing_source_url,
                pricing_retrieved_at=pricing_retrieved_at,
            ),
            "production_changed": False,
            "network_or_download_requires_explicit_flag": True,
            "prepare_models": bool(prepare_models),
            "include_llm": bool(include_llm),
        },
        "debug": {
            "candidate_ids_by_system": {
                system: candidate_ids.copy() for system in debug_systems
            },
            "phase_order": [
                "fixture_validation", "seed_store", "baseline",
                "freeze_shared_candidates", "calibration", "freeze_configuration",
                "held_out", "gates", "recommendation",
            ],
        },
    }


def _reproduction_command(
    *, cases_or_path, report_dir, model_cache, prepare_models, include_llm,
    input_price, output_price, pricing_source_url, pricing_retrieved_at,
) -> str:
    fixtures = (
        Path(cases_or_path)
        if isinstance(cases_or_path, (str, Path))
        else _DEFAULT_FIXTURES
    )
    arguments = [
        "python", "-m", "evaluation.tournament",
        "--fixtures", fixtures.as_posix(),
        "--report-dir", Path(report_dir).as_posix(),
        "--model-cache", Path(model_cache).as_posix(),
    ]
    if prepare_models:
        arguments.append("--prepare-models")
    if include_llm:
        arguments.extend([
            "--include-llm",
            "--llm-input-usd-per-million", str(float(input_price)),
            "--llm-output-usd-per-million", str(float(output_price)),
        ])
    if pricing_source_url:
        arguments.extend(["--pricing-source-url", pricing_source_url])
    if pricing_retrieved_at:
        arguments.extend(["--pricing-retrieved-at", pricing_retrieved_at])
    return subprocess.list2cmdline(arguments)


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the offline reranker tournament")
    parser.add_argument("--fixtures", type=Path, default=_DEFAULT_FIXTURES)
    parser.add_argument("--report-dir", type=Path, default=_DEFAULT_REPORT_DIR)
    parser.add_argument("--model-cache", type=Path, default=_DEFAULT_MODEL_CACHE)
    parser.add_argument("--prepare-models", action="store_true")
    parser.add_argument("--include-llm", action="store_true")
    parser.add_argument("--llm-input-usd-per-million", type=float, default=0.0)
    parser.add_argument("--llm-output-usd-per-million", type=float, default=0.0)
    parser.add_argument("--pricing-source-url")
    parser.add_argument("--pricing-retrieved-at")
    return parser


def parse_cli_args(argv=None) -> argparse.Namespace:
    return build_cli_parser().parse_args(argv)


def main(argv=None) -> int:
    """Run the isolated experiment, persist completed evidence, and map its decision."""
    parsed = parse_cli_args(argv)
    result = run_tournament(
        cases_or_path=parsed.fixtures,
        include_llm=parsed.include_llm,
        model_cache=parsed.model_cache,
        report_dir=parsed.report_dir,
        prepare_models=parsed.prepare_models,
        llm_input_usd_per_million=parsed.llm_input_usd_per_million,
        llm_output_usd_per_million=parsed.llm_output_usd_per_million,
        pricing_source_url=parsed.pricing_source_url,
        pricing_retrieved_at=parsed.pricing_retrieved_at,
    )
    decision = result.get("decision", "INCOMPLETE")
    if decision in {"LOCAL_PASS", "LLM_ONLY_PASS", "FAIL"}:
        try:
            write_reports(result, parsed.report_dir)
        except Exception as exc:
            print(f"Report write failed: {exc}", file=sys.stderr)
            return 2
    print(json.dumps({"decision": decision}, indent=2))
    return 0 if decision == "LOCAL_PASS" else 1 if decision in {"LLM_ONLY_PASS", "FAIL"} else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
