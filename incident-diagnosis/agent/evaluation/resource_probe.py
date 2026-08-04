"""Fresh-process resource measurements for local tournament challengers."""

from __future__ import annotations

import importlib.metadata
import math
import multiprocessing
import os
from pathlib import Path
from queue import Empty
from time import perf_counter
from typing import Callable

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


_EVALUATION_ROOT_DISTRIBUTIONS = (
    "huggingface-hub",
    "numpy",
    "onnxruntime",
    "psutil",
    "transformers",
)
_BASE_ROOT_DISTRIBUTIONS = (
    "fakeredis",
    "flask",
    "flask-cors",
    "rank-bm25",
    "redis",
    "requests",
)


def nearest_rank_percentile(values: tuple[float, ...], percentile: float) -> float:
    """Return a nearest-rank percentile (p in the closed interval 0..1)."""
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0.0 <= percentile <= 1.0:
        raise ValueError("percentile must be between zero and one")
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _dependency_closure(roots: tuple[str, ...]) -> dict[str, object]:
    pending = [canonicalize_name(name) for name in roots]
    closure = {}
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        closure[name] = distribution
        for raw_requirement in distribution.requires or ():
            requirement = Requirement(raw_requirement)
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            dependency = canonicalize_name(requirement.name)
            if dependency not in closure:
                pending.append(dependency)
    return closure


def _distribution_size_mb(
    evaluation_roots: tuple[str, ...] = _EVALUATION_ROOT_DISTRIBUTIONS,
    base_roots: tuple[str, ...] = _BASE_ROOT_DISTRIBUTIONS,
) -> float:
    """Installed evaluation dependency closure absent from the base closure."""
    evaluation = _dependency_closure(evaluation_roots)
    base = _dependency_closure(base_roots)
    total = 0
    seen: set[Path] = set()
    for name, distribution in evaluation.items():
        if name in base:
            continue
        for relative in distribution.files or ():
            path = Path(distribution.locate_file(relative))
            try:
                resolved = path.resolve()
                if resolved not in seen and resolved.is_file():
                    seen.add(resolved)
                    total += resolved.stat().st_size
            except OSError:
                continue
    return total / (1024 * 1024)


def _model_footprint_mb(model_dir: Path) -> float:
    total = 0
    for path in Path(model_dir).rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total / (1024 * 1024)


def _rss_mb(process) -> float:
    return float(process.memory_info().rss) / (1024 * 1024)


def _worker(queue, request: dict) -> None:
    """Load production runtime plus one reranker and evaluate inside the child."""
    try:
        os.environ["OMP_NUM_THREADS"] = "1"
        os.environ["ORT_NUM_THREADS"] = "1"

        # Deliberately import both production modules before model load.  Their
        # resident memory is part of the runtime footprint being measured.
        import memory  # noqa: F401
        import interpreter  # noqa: F401
        import psutil

        from evaluation.local_reranker import rerank_local
        from evaluation.onnx_backend import OnnxCrossEncoder

        process = psutil.Process(os.getpid())
        started = perf_counter()
        backend = OnnxCrossEncoder(Path(request["model_dir"]), request["spec"])
        cold_load_ms = (perf_counter() - started) * 1000
        rss_samples_mb = [_rss_mb(process)]
        peak_rss_mb = rss_samples_mb[0]
        latency_values: list[float] = []
        runs = []
        for _ in range(request["repetitions"]):
            run = {}
            for case, candidates in request["batches"]:
                trace = rerank_local(
                    (case, candidates), backend, request["floor"], limit=3
                )
                run[case.id] = trace
                latency_values.append(float(trace.latency_ms))
                rss_samples_mb.append(_rss_mb(process))
                peak_rss_mb = max(peak_rss_mb, rss_samples_mb[-1])
            runs.append(run)
        queue.put({
            "available": True,
            "cold_load_ms": cold_load_ms,
            "latencies_ms": latency_values,
            "peak_rss_mb": peak_rss_mb,
            "rss_samples_mb": rss_samples_mb,
            "runs": runs,
        })
    except BaseException as exc:  # child failures must remain system-local
        queue.put({
            "available": False,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        })


def _spawn_process_probe(request: dict) -> dict:
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_worker, args=(queue, request))
    process.start()
    timeout_seconds = float(request["timeout_seconds"])
    started = perf_counter()
    try:
        # Drain while the child is alive. Joining first can deadlock when the
        # Queue feeder blocks on an oversized payload and waits for a reader.
        payload = queue.get(timeout=timeout_seconds)
    except Empty as exc:
        if process.is_alive():
            process.terminate()
            process.join(5)
            queue.close()
            raise TimeoutError(
                f"resource worker exceeded {timeout_seconds} seconds"
            ) from exc
        queue.close()
        raise RuntimeError(
            f"resource worker exited {process.exitcode} without a result"
        ) from exc
    remaining = max(0.0, timeout_seconds - (perf_counter() - started))
    process.join(remaining)
    if process.is_alive():
        process.terminate()
        process.join(5)
        queue.close()
        raise TimeoutError(f"resource worker exceeded {timeout_seconds} seconds")
    queue.close()
    return payload


def _serialized_error(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)}


def measure_local_adapter(
    *,
    name: str,
    artifact_path: Path,
    model_dir: Path,
    spec,
    batches: tuple,
    floor: float,
    repetitions: int = 1,
    process_probe: Callable[[dict], dict] | None = None,
    dependency_size_probe: Callable[[], float] | None = None,
    timeout_seconds: float = 600.0,
) -> dict:
    """Measure one local model in a newly spawned child process.

    The injectable probes keep unit tests offline while the default path uses
    a real spawn context.  Every failure is returned as data so another
    challenger can continue.
    """
    artifact_path = Path(artifact_path)
    dependency_size_probe = dependency_size_probe or _distribution_size_mb
    artifact_mb = None
    dependency_mb = None
    try:
        if repetitions < 1:
            raise ValueError("repetitions must be positive")
        if not artifact_path.is_file():
            raise FileNotFoundError(f"model artifact is missing: {artifact_path}")
        artifact_mb = _model_footprint_mb(Path(model_dir))
        dependency_mb = float(dependency_size_probe())
        request = {
            "name": name,
            "artifact_path": str(artifact_path),
            "model_dir": str(Path(model_dir)),
            "spec": spec,
            "batches": tuple(batches),
            "floor": float(floor),
            "repetitions": repetitions,
            "timeout_seconds": float(timeout_seconds),
        }
        payload = (process_probe or _spawn_process_probe)(request)
        if not isinstance(payload, dict):
            raise TypeError("process probe must return a dictionary")
        if not payload.get("available"):
            error = payload.get("error") or {
                "type": "WorkerError",
                "message": "local resource worker was unavailable",
            }
            return {
                "name": name,
                "available": False,
                "error": error,
                "artifact_mb": artifact_mb,
                "estimated_container_delta_mb": artifact_mb + dependency_mb,
                "container_delta_is_estimate": True,
                "container_delta_basis": (
                    "complete installed evaluation-only dependency closure absent "
                    "from the base runtime plus recursive model/tokenizer/config "
                    "footprint; no production image was built"
                ),
                "runs": [],
            }
        latencies = tuple(float(value) for value in payload.get("latencies_ms", ()))
        return {
            "name": name,
            "available": True,
            "error": None,
            "artifact_mb": artifact_mb,
            "estimated_container_delta_mb": artifact_mb + dependency_mb,
            "container_delta_is_estimate": True,
            "container_delta_basis": (
                "complete installed evaluation-only dependency closure absent "
                "from the base runtime plus recursive model/tokenizer/config "
                "footprint; no production image was built"
            ),
            "cold_load_ms": float(payload.get("cold_load_ms", 0.0)),
            "latencies_ms": list(latencies),
            "p50_ms": nearest_rank_percentile(latencies, 0.50) if latencies else 0.0,
            "p95_ms": nearest_rank_percentile(latencies, 0.95) if latencies else 0.0,
            "peak_rss_mb": float(payload.get("peak_rss_mb", 0.0)),
            "rss_samples_mb": [
                float(value) for value in payload.get("rss_samples_mb", ())
            ],
            "runs": payload.get("runs", []),
        }
    except BaseException as exc:
        result = {
            "name": name,
            "available": False,
            "error": _serialized_error(exc),
            "artifact_mb": artifact_mb,
            "estimated_container_delta_mb": (
                None
                if artifact_mb is None or dependency_mb is None
                else artifact_mb + dependency_mb
            ),
            "container_delta_is_estimate": True,
            "container_delta_basis": (
                "complete installed evaluation-only dependency closure absent "
                "from the base runtime plus recursive model/tokenizer/config "
                "footprint; no production image was built"
            ),
            "runs": [],
        }
        return result
