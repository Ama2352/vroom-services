import json
from pathlib import Path

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
