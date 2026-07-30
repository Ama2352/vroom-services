"""Reproducible, fully offline BM25 retrieval proof."""

import json
from dataclasses import asdict
from pathlib import Path

from evaluation.baseline import rank_current_coverage, seed_store
from evaluation.bm25_variants import rank_bm25
from evaluation.fixture_loader import load_cases
from evaluation.metrics import calibrate_variant, passes_gate, summarize
from evaluation.models import MetricSummary, RetrievalOutcome, VariantConfig


VARIANTS = (
    VariantConfig("baseline_plain", "baseline", "plain"),
    VariantConfig("baseline_joined", "baseline", "joined"),
    VariantConfig("rich_plain", "rich", "plain"),
    VariantConfig("rich_joined", "rich", "joined"),
)


def _metrics(summary: MetricSummary) -> dict:
    return {
        **asdict(summary),
        "top1_accuracy": summary.top1_accuracy,
        "recall_at_3": summary.recall_at_3,
        "false_positive_rate": summary.false_positive_rate,
    }


def _ranked(outcome: RetrievalOutcome) -> list[dict]:
    return [
        {
            "knowledge_key": candidate.knowledge_key,
            "raw_score": candidate.score,
            "score": candidate.score,
            "source": candidate.source,
            "source_id": candidate.source_id,
            "matched_terms": list(candidate.matched_terms),
        }
        for candidate in outcome.candidates
    ]


def _run(cases, ranker) -> dict[str, RetrievalOutcome]:
    rdb = seed_store()
    return {case.id: ranker(rdb, case) for case in cases}


def _render_markdown(result: dict) -> str:
    selected = result.get("selected_variant")
    selected_name = selected["name"] if selected else "none"
    selected_floor = f"{selected['threshold']:.3f}" if selected else "n/a"
    lines = [
        "# BM25 Retrieval Proof",
        "",
        f"**Status:** {result['status']}",
        "**Infrastructure required:** none",
        "**LLM calls:** none",
        f"**Selected variant:** {selected_name}",
        f"**Calibrated raw-score floor:** {selected_floor}",
        (
            f"**Selection rationale:** {selected['selection_rationale']}"
            if selected
            else "**Selection rationale:** no challenger passed calibration; baseline traces used"
        ),
        "",
    ]

    for split in ("calibration", "held_out"):
        lines.extend([
            f"## {split.replace('_', ' ').title()} metrics",
            "",
            "| System | Top-1 | Recall@3 | False-positive | Forbidden | Exact failures |",
            "|---|---:|---:|---:|---:|---:|",
        ])
        rows = [("baseline", result["baseline"][split])]
        if selected:
            rows.append((selected_name, selected[f"{split}_metrics"]))
        for name, metrics in rows:
            lines.append(
                f"| {name} | {metrics['top1_accuracy']:.3f} | "
                f"{metrics['recall_at_3']:.3f} | "
                f"{metrics['false_positive_rate']:.3f} | "
                f"{metrics['forbidden_acceptances']} | "
                f"{metrics['exact_failures']} |"
            )
        lines.append("")

    lines.extend(["## Challenger calibration outcomes", ""])
    for variant in result["variants"]:
        metrics = variant["calibration_metrics"]
        threshold = (
            f"{variant['threshold']:.3f}"
            if variant["threshold"] is not None
            else "n/a"
        )
        lines.append(
            f"- **{variant['name']}**: calibrated={str(variant['calibrated']).lower()}, "
            f"threshold={threshold}, "
            f"false_positive={metrics['false_positive_rate']:.3f}, "
            f"top1={metrics['top1_accuracy']:.3f}, "
            f"recall_at_3={metrics['recall_at_3']:.3f}, "
            f"exact_failures={metrics['exact_failures']}"
        )
    lines.extend(["", "## Per-case traces", ""])
    for case in result["cases"]:
        lines.extend([
            f"### {case['id']}",
            "",
            f"- Split: {case['split']}",
            f"- Expected mode: {case['expected_mode']}",
            f"- Expected keys: {', '.join(case['expected_keys']) or 'none'}",
            f"- Selected mode: {case['selected_mode']}",
            f"- Exact ambiguity: {str(case['exact_ambiguous']).lower()}",
            f"- Rationale: {case['rationale']}",
        ])
        if not case["ranked"]:
            lines.append("- Ranked: none")
        else:
            lines.append("- Ranked:")
            for item in case["ranked"]:
                terms = ", ".join(item["matched_terms"]) or "none"
                lines.append(
                    f"  - {item['knowledge_key']} | raw_score={item['raw_score']:.3f} "
                    f"| matched_terms={terms} | source={item['source']}:{item['source_id']}"
                )
        lines.append("")
    return "\n".join(lines)


def run_benchmark(fixture_path: Path, report_dir: Path) -> dict:
    cases = load_cases(fixture_path)
    calibration = tuple(case for case in cases if case.split == "calibration")
    held_out = tuple(case for case in cases if case.split == "held_out")

    baseline_outcomes = _run(cases, rank_current_coverage)
    baseline_calibration = summarize(
        calibration, {case.id: baseline_outcomes[case.id] for case in calibration}
    )
    baseline_held_out = summarize(
        held_out, {case.id: baseline_outcomes[case.id] for case in held_out}
    )

    variant_results = []
    calibrated_runs = []
    for config in VARIANTS:
        rdb = seed_store()
        raw_calibration = {
            case.id: rank_bm25(rdb, case, config) for case in calibration
        }
        calibrated = calibrate_variant(
            rdb, calibration, config, baseline_calibration
        )
        effective = calibrated or config
        effective_outcomes = {
            case.id: rank_bm25(rdb, case, effective) for case in calibration
        }
        calibration_metrics = summarize(calibration, effective_outcomes)
        item = {
            "name": config.name,
            "query_variant": config.query_variant,
            "history_variant": config.history_variant,
            "calibrated": calibrated is not None,
            "threshold": calibrated.threshold if calibrated else None,
            "uncalibrated_metrics": _metrics(
                summarize(calibration, raw_calibration)
            ),
            "calibration_metrics": _metrics(calibration_metrics),
        }
        variant_results.append(item)
        if calibrated:
            calibrated_runs.append((calibration_metrics, calibrated, item))

    calibrated_runs.sort(key=lambda entry: (
        entry[0].false_positive_rate,
        -entry[0].top1_accuracy,
        -entry[0].recall_at_3,
        entry[1].name,
    ))

    selected_result = None
    if calibrated_runs:
        selected_calibration, selected_config, _ = calibrated_runs[0]
        selected_outcomes = _run(
            cases, lambda rdb, case: rank_bm25(rdb, case, selected_config)
        )
        selected_held_out = summarize(
            held_out,
            {case.id: selected_outcomes[case.id] for case in held_out},
        )
        selected_result = {
            "name": selected_config.name,
            "query_variant": selected_config.query_variant,
            "history_variant": selected_config.history_variant,
            "threshold": selected_config.threshold,
            "calibration_metrics": _metrics(selected_calibration),
            "held_out_metrics": _metrics(selected_held_out),
            "selection_rationale": (
                "Lowest calibration false-positive rate, then highest top-1 "
                "accuracy, highest recall@3, and lexicographic variant name."
            ),
        }
        status = (
            "PASS"
            if passes_gate(selected_held_out, baseline_held_out)
            else "FAIL"
        )
        trace_outcomes = selected_outcomes
        trace_system = selected_config.name
    else:
        status = "FAIL"
        trace_outcomes = baseline_outcomes
        trace_system = "baseline_fallback"

    result = {
        "status": status,
        "baseline": {
            "calibration": _metrics(baseline_calibration),
            "held_out": _metrics(baseline_held_out),
        },
        "variants": variant_results,
        "selected_variant": selected_result,
        "trace_system": trace_system,
        "fallback_reason": (
            None
            if selected_result
            else "No challenger passed calibration; traces use the current baseline."
        ),
        "cases": [
            {
                "id": case.id,
                "split": case.split,
                "expected_keys": list(case.expected_keys),
                "expected_mode": case.expected_mode,
                "forbidden_keys": list(case.forbidden_keys),
                "selected_mode": trace_outcomes[case.id].mode,
                "exact_ambiguous": trace_outcomes[case.id].exact_ambiguous,
                "rationale": case.rationale,
                "ranked": _ranked(trace_outcomes[case.id]),
            }
            for case in cases
        ],
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "bm25-proof.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (report_dir / "bm25-proof.md").write_text(
        _render_markdown(result), encoding="utf-8"
    )
    return result


def main() -> int:
    agent_dir = Path(__file__).parents[1]
    result = run_benchmark(
        agent_dir / "evaluation/fixtures/retrieval_cases.json",
        agent_dir / "evaluation/reports",
    )
    selected = result.get("selected_variant")
    selected_name = selected["name"] if selected else "none"
    print(f"BM25 retrieval proof: {result['status']} (selected={selected_name})")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
