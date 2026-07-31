import json
from pathlib import Path

import pytest

import evaluation.tournament as tournament
from evaluation.models import RankedCandidate, RetrievalOutcome
from evaluation.tournament_models import DecisionTrace
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


def test_markdown_names_failed_and_unavailable_system_reasons(result_factory):
    result = result_factory()
    result["systems"]["mixedbread_xsmall"]["passed"] = False
    result["systems"]["mixedbread_xsmall"]["failure_reasons"] = [
        "forbidden candidate accepted"
    ]
    result["systems"]["llm"].update({
        "status": "unavailable",
        "error": {"type": "Unavailable", "message": "GROQ_KEY is required"},
        "calibration": None,
        "held_out": None,
        "failure_reasons": ["GROQ_KEY is required"],
    })

    markdown = render_concise_markdown(result)

    assert "mixedbread_xsmall failed: forbidden candidate accepted" in markdown
    assert "llm unavailable: GROQ_KEY is required" in markdown


def test_markdown_caps_long_failure_reason_without_dropping_required_sections(result_factory):
    result = result_factory()
    result["informative_failures"] = [{
        "case_id": "dns_no_match", "failure_type": "hard_negative",
        "reason": "DNS abstained",
    }, {
        "case_id": "long_trace", "failure_type": "false_positive",
        "reason": "evidence " * 2_000,
    }]
    markdown = render_concise_markdown(result)
    assert len(markdown.split()) <= 1200
    assert "## Reproduce" in markdown


@pytest.mark.parametrize("key", [
    "GROQ_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "X-API-Key",
    "Authorization", "X-Auth-Token", "x_auth_token", "access_token",
    "client_token", "credentials", "Bearer Authorization", "Basic Authorization",
])
def test_reports_reject_credential_key_variants_before_writing(tmp_path, result_factory, key):
    result = result_factory()
    result["environment"]["nested"] = {key: "do-not-write-this-secret"}
    with pytest.raises(ValueError):
        write_reports(result, tmp_path)
    assert not (tmp_path / "reranker-tournament.json").exists()


def test_local_and_llm_per_case_failures_are_serialized_and_globally_prioritized():
    forbidden = RankedCandidate(
        "crashloop", 1.0, "knowledge", "test", (), "cause", "fix"
    )

    class LocalForbiddenAdapter:
        def evaluate(self, batch, *, floor):
            case, candidates = batch
            if case.id == "oauth_expired_held_no_match":
                return DecisionTrace(RetrievalOutcome("advisory", (forbidden,)))
            return DecisionTrace(candidates)

        def freeze(self, name, value):
            pass

    class LlmMissAdapter:
        def evaluate(self, batch):
            case, candidates = batch
            if case.split == "held_out":
                return DecisionTrace(RetrievalOutcome("none", ()))
            return DecisionTrace(candidates)

        def freeze(self, name, value):
            pass

    result = tournament.run_tournament(
        Path(__file__).parents[1] / "evaluation/fixtures/retrieval_cases_v2.json",
        adapters={"minilm": LocalForbiddenAdapter(), "llm": LlmMissAdapter()},
        include_llm=True,
    )
    traces = result["informative_failures"]
    assert any(
        trace["system"] == "minilm" and trace["failure_type"] == "forbidden_acceptance"
        for trace in traces
    )
    assert any(
        trace["system"] == "llm" and trace["failure_type"] == "missed_positive"
        for trace in traces
    )
    local_trace = next(
        trace for trace in traces
        if trace["system"] == "minilm" and trace["failure_type"] == "forbidden_acceptance"
    )
    result["informative_failures"] = [
        {"case_id": "dns_no_match", "failure_type": "hard_negative", "reason": "abstained"},
        {"case_id": "baseline_miss", "system": "baseline", "failure_type": "missed_positive", "reason": "missed"},
        local_trace,
    ]
    failures = render_concise_markdown(result).split("## Informative failures", 1)[1].split("## Decision", 1)[0]
    assert "`oauth_expired_held_no_match`" in failures


def test_informative_failure_presentation_uses_required_priority(result_factory):
    result = result_factory()
    result["informative_failures"] = [
        {"case_id": "false", "failure_type": "false_positive", "reason": "wrong advisory"},
        {"case_id": "forbidden", "failure_type": "forbidden_acceptance", "reason": "forbidden key"},
        {"case_id": "missed", "failure_type": "missed_positive", "reason": "abstained"},
    ]
    markdown = render_concise_markdown(result)
    failures = markdown.split("## Informative failures", 1)[1].split("## Decision", 1)[0]
    assert "`forbidden`" in failures
    assert "`false`" not in failures


def test_telemetry_distinguishes_malformed_llm_output_from_provider_failure(result_factory):
    result = result_factory()
    result["systems"]["llm"]["provider_failures"] = 0
    result["llm_repetitions"] = {"case": {"runs": [
        {"parse_outcome": "error", "error": "JSONDecodeError: invalid JSON"},
        {"parse_outcome": "error", "error": "TimeoutError: provider timeout"},
    ]}}
    assert "1 malformed and 1 provider failure(s)" in render_concise_markdown(result)


def test_tournament_serializes_dns_and_actual_per_case_failures():
    result = tournament.run_tournament(
        Path(__file__).parents[1] / "evaluation/fixtures/retrieval_cases_v2.json"
    )
    traces = result["informative_failures"]
    assert any(trace["case_id"] == "dns_no_match" for trace in traces)
    assert all("failure_type" in trace for trace in traces)


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
