import json

import pytest

import evaluation.tournament as tournament
from evaluation.tournament_reporting import render_concise_markdown, write_reports


@pytest.fixture
def result_factory():
    def build(*, decision="FAIL", false_positives=0, no_match_cases=10):
        metrics = {
            "positive_cases": 30,
            "no_match_cases": no_match_cases,
            "top1_correct": 24,
            "top3_correct": 27,
            "false_positives": false_positives,
            "forbidden_acceptances": 0,
            "exact_failures": 0,
            "reciprocal_rank_sum": 25.5,
            "correct_abstentions": no_match_cases - false_positives,
            "top1_accuracy": 0.8,
            "recall_at_3": 0.9,
            "false_positive_rate": false_positives / no_match_cases,
            "mean_reciprocal_rank": 0.85,
            "abstention_accuracy": (no_match_cases - false_positives) / no_match_cases,
        }
        systems = {}
        for name, kind in (
            ("baseline", "baseline"), ("bm25", "bm25"),
            ("minilm", "local"), ("mixedbread_xsmall", "local"), ("llm", "llm"),
        ):
            systems[name] = {
                "name": name, "kind": kind, "status": "available", "error": None,
                "calibration": metrics, "held_out": metrics, "threshold": 0.123456789,
                "stable": True, "passed": name == "minilm",
                "operational": {
                    "artifact_mb": 17.0, "estimated_container_delta_mb": 25.0,
                    "cold_load_ms": 4.0, "p50_ms": 2.0, "p95_ms": 5.0,
                    "peak_rss_mb": 42.0, "request_count": 3, "input_tokens": 20,
                    "output_tokens": 10, "current_spend_usd": 0.0,
                    "theoretical_spend_usd": 0.0001,
                },
                "failure_reasons": [],
            }
        return {
            "schema_version": 1, "generated_at": "2026-07-31T00:00:00+00:00",
            "decision": decision, "recommendation": {"name": "minilm", "kind": "local"},
            "environment": {"prompt": {"text": "frozen prompt", "sha256": "abc"}},
            "dataset": {"case_count": 40, "calibration_count": 20, "held_out_count": 20,
                        "positive_count": 30, "no_match_count": no_match_cases},
            "shared_candidates": {"dns_no_match": {"mode": "none", "candidates": []}},
            "systems": systems, "llm_repetitions": {},
            "informative_failures": [{"case_id": "dns_no_match", "reason": "DNS hard negative abstained"},
                                     {"case_id": "tls_no_match", "reason": "false positive rejected"}],
            "failure_reasons": [],
            "reproduction": {"command": "python -m evaluation.tournament"},
        }
    return build


def test_reports_preserve_full_json_and_concise_required_sections(tmp_path, result_factory):
    result = result_factory(decision="FAIL")
    json_path, markdown_path = write_reports(result, tmp_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert payload == result
    assert payload["decision"] == "FAIL"
    assert len(markdown.split()) <= 1200
    for heading in (
        "# Reranker Tournament", "## Why", "## Systems", "## Results",
        "## Informative failures", "## Decision", "## Limitations",
        "## Interview explanation", "## Reproduce",
    ):
        assert heading in markdown


def test_report_prints_false_positive_counts_beside_rates(result_factory):
    markdown = render_concise_markdown(result_factory(false_positives=1, no_match_cases=10))
    assert "1/10" in markdown
    assert "10.0%" in markdown


def test_cli_returns_zero_only_for_local_pass(monkeypatch):
    monkeypatch.setattr(tournament, "run_tournament", lambda **kwargs: {"decision": "LOCAL_PASS"})
    monkeypatch.setattr(tournament, "write_reports", lambda result, path: (path, path))
    assert tournament.main([]) == 0


def test_cli_returns_two_when_completed_report_write_fails(monkeypatch):
    monkeypatch.setattr(tournament, "run_tournament", lambda **kwargs: {"decision": "FAIL"})
    monkeypatch.setattr(tournament, "write_reports", lambda result, path: (_ for _ in ()).throw(RuntimeError("disk error")))
    assert tournament.main([]) == 2


@pytest.mark.parametrize("decision, expected", [("LLM_ONLY_PASS", 1), ("FAIL", 1), ("INCOMPLETE", 2)])
def test_cli_maps_non_local_decisions_to_required_exit_codes(monkeypatch, decision, expected):
    monkeypatch.setattr(tournament, "run_tournament", lambda **kwargs: {"decision": decision})
    monkeypatch.setattr(tournament, "write_reports", lambda result, path: (path, path))
    assert tournament.main([]) == expected
