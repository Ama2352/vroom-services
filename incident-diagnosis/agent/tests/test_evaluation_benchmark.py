import json
import re
from pathlib import Path

import evaluation.benchmark as benchmark
from evaluation.benchmark import run_benchmark


FIXTURE_PATH = Path(__file__).parents[1] / "evaluation/fixtures/retrieval_cases.json"


def test_benchmark_writes_machine_and_human_reports(tmp_path):
    result = run_benchmark(FIXTURE_PATH, tmp_path)
    json_path = tmp_path / "bm25-proof.json"
    md_path = tmp_path / "bm25-proof.md"
    assert json_path.exists()
    assert md_path.exists()
    assert json.loads(json_path.read_text())["status"] in {"PASS", "FAIL"}
    assert "# BM25 Retrieval Proof" in md_path.read_text()
    assert result["status"] in {"PASS", "FAIL"}


def test_report_contains_baseline_all_variants_and_per_case_traces(tmp_path):
    result = run_benchmark(FIXTURE_PATH, tmp_path)
    assert result["baseline"]["held_out"]
    assert len(result["variants"]) == 4
    assert {v["name"] for v in result["variants"]} == {
        "baseline_plain",
        "baseline_joined",
        "rich_plain",
        "rich_joined",
    }
    assert len(result["cases"]) == 20
    assert all("ranked" in case for case in result["cases"])
    assert all(
        "matched_terms" in item
        for case in result["cases"]
        for item in case["ranked"]
    )


def test_selected_variant_is_frozen_before_held_out_gate(tmp_path):
    result = run_benchmark(FIXTURE_PATH, tmp_path)
    selected = result.get("selected_variant")
    if selected is not None:
        assert "calibration_metrics" in selected
        assert "threshold" in selected
        assert "held_out_metrics" in selected


def test_markdown_selected_floor_uses_exactly_three_decimals(tmp_path):
    result = run_benchmark(FIXTURE_PATH, tmp_path)
    selected = result["selected_variant"]
    markdown = (tmp_path / "bm25-proof.md").read_text()
    assert (
        f"**Calibrated raw-score floor:** {selected['threshold']:.3f}"
        in markdown
    )
    floor = re.search(
        r"\*\*Calibrated raw-score floor:\*\* ([^\n]+)", markdown
    ).group(1)
    assert re.fullmatch(r"\d+\.\d{3}", floor)


def test_markdown_challenger_thresholds_use_exactly_three_decimals(tmp_path):
    result = run_benchmark(FIXTURE_PATH, tmp_path)
    markdown = (tmp_path / "bm25-proof.md").read_text()
    for variant in result["variants"]:
        if variant["threshold"] is not None:
            assert (
                f"**{variant['name']}**: calibrated=true, "
                f"threshold={variant['threshold']:.3f},"
            ) in markdown
    rendered = re.findall(r"calibrated=true, threshold=([^,]+),", markdown)
    assert rendered
    assert all(re.fullmatch(r"\d+\.\d{3}", value) for value in rendered)


def test_json_retains_raw_threshold_float(tmp_path):
    result = run_benchmark(FIXTURE_PATH, tmp_path)
    report = json.loads((tmp_path / "bm25-proof.json").read_text())
    assert (
        report["selected_variant"]["threshold"]
        == result["selected_variant"]["threshold"]
    )
    assert any(
        variant["threshold"] != round(variant["threshold"], 3)
        for variant in report["variants"]
        if variant["threshold"] is not None
    )


def test_cli_returns_zero_for_pass(monkeypatch, capsys):
    monkeypatch.setattr(
        benchmark,
        "run_benchmark",
        lambda fixture_path, report_dir: {
            "status": "PASS",
            "selected_variant": {"name": "rich_joined"},
        },
    )
    assert benchmark.main() == 0
    assert "PASS (selected=rich_joined)" in capsys.readouterr().out


def test_cli_returns_one_for_fail_without_selection(monkeypatch, capsys):
    monkeypatch.setattr(
        benchmark,
        "run_benchmark",
        lambda fixture_path, report_dir: {
            "status": "FAIL",
            "selected_variant": None,
        },
    )
    assert benchmark.main() == 1
    assert "FAIL (selected=none)" in capsys.readouterr().out
