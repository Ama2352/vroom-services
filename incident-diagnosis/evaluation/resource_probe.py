"""Measure one local reranker in a fresh Python process.

Running each contender alone prevents a previously loaded model from inflating
its RSS measurement in Google Colab.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

import numpy as np
import psutil


ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT.parent / "agent"), str(ROOT)]

from benchmark import load_cases, load_model_specs, load_snapshot, retrieve_case
from retrieval.reranker import MiniLMReranker, ModelSpec, OnnxCrossEncoder


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args()

    specs = load_model_specs(ROOT / "model_specs.json")
    spec_data = specs[args.name]
    spec = ModelSpec(**spec_data)
    cases = load_cases(ROOT / "fixtures" / "retrieval_cases.json")
    snapshot = load_snapshot(ROOT / "fixtures" / "knowledge_snapshot.json")

    process = psutil.Process()
    started = perf_counter()
    reranker = MiniLMReranker(OnnxCrossEncoder(args.model_dir, spec))
    cold_load_ms = (perf_counter() - started) * 1000
    peak_rss_mb = process.memory_info().rss / 1024**2
    latencies = []

    for case in cases:
        started = perf_counter()
        retrieve_case(case, snapshot, reranker)
        latencies.append((perf_counter() - started) * 1000)
        peak_rss_mb = max(peak_rss_mb, process.memory_info().rss / 1024**2)

    artifact = args.model_dir / spec_data["onnx_file"]
    print(json.dumps({
        "artifact_mb": artifact.stat().st_size / 1024**2,
        "cold_load_ms": cold_load_ms,
        "p50_ms": float(np.percentile(latencies, 50)),
        "p95_ms": float(np.percentile(latencies, 95)),
        "peak_rss_mb": peak_rss_mb,
    }))


if __name__ == "__main__":
    main()
