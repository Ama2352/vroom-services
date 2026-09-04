import json
from pathlib import Path
import sys

EVALUATION_ROOT = Path(__file__).parents[1]
AGENT_ROOT = EVALUATION_ROOT.parent / "agent"
sys.path.insert(0, str(AGENT_ROOT))
sys.path.insert(0, str(EVALUATION_ROOT))

import benchmark

from benchmark import (
    EvaluationResult,
    IdentityReranker,
    build_template,
    calibrate_score_floor,
    field_coverage,
    load_cases,
    load_model_specs,
    load_snapshot,
    passes_gate,
    retrieve_case,
    run_system,
    run_raw_ranking,
    evaluate_selection_gate,
    validate_semantic_cases,
)
from retrieval.models import EvidenceCandidate, EvidenceRetrievalMode


CASES_PATH = EVALUATION_ROOT / "fixtures" / "retrieval_cases.json"
SEMANTIC_CASES_PATH = EVALUATION_ROOT / "fixtures" / "semantic_disambiguation_cases.json"
SNAPSHOT_PATH = EVALUATION_ROOT / "fixtures" / "historical_context_enriched_snapshot.json"
MODEL_SPECS_PATH = EVALUATION_ROOT / "model_specs.json"
NOTEBOOK_PATH = EVALUATION_ROOT / "raw_model_selection_colab.ipynb"


def test_exact_case_matches_one_current_schema_fingerprint():
    cases = load_cases(CASES_PATH)
    snapshot = load_snapshot(SNAPSHOT_PATH)
    exact_case = next(case for case in cases if case.expected_mode == "exact")

    exact_examples = [
        example for example in snapshot["examples"]
        if example["exact_reusable"]
        and example["fingerprint"] == build_template(exact_case).fingerprint()
    ]

    assert len({example["knowledge_key"] for example in exact_examples}) == 1
    assert exact_examples[0]["knowledge_key"] in exact_case.expected_keys


class FailIfCalled:
    def rerank(self, _query, _candidates):
        raise AssertionError("exact reuse must bypass the reranker")


class RecordingReranker:
    def __init__(self):
        self.candidate_texts = ()
        self.candidate_keys = ()

    def rerank(self, _query, candidates):
        self.candidate_texts = tuple(candidate.serialized for candidate in candidates)
        self.candidate_keys = tuple(candidate.knowledge_key for candidate in candidates)
        return candidates


def test_exact_case_runs_through_clean_retrieval_without_reranking():
    exact_case = next(case for case in load_cases(CASES_PATH) if case.expected_mode == "exact")

    result = retrieve_case(exact_case, load_snapshot(SNAPSHOT_PATH), FailIfCalled())

    assert result.mode is EvidenceRetrievalMode.EXACT
    assert result.candidates[0].knowledge_key in exact_case.expected_keys


def test_nearest_case_reranks_hint_backed_bm25_candidates():
    case = next(case for case in load_cases(CASES_PATH) if case.case_id == "image_pull_advisory")
    reranker = RecordingReranker()

    result = retrieve_case(case, load_snapshot(SNAPSHOT_PATH), reranker)

    assert result.mode is EvidenceRetrievalMode.NEAREST
    assert result.candidates[0].knowledge_key == "image_pull"
    assert any("approved_hints:" in text for text in reranker.candidate_texts)


def test_unsupported_case_abstains_without_reranking_generic_terms():
    case = next(case for case in load_cases(CASES_PATH) if case.case_id == "sparse_no_match")

    result = retrieve_case(case, load_snapshot(SNAPSHOT_PATH), FailIfCalled())

    assert result.mode is EvidenceRetrievalMode.NONE
    assert result.candidates == ()


def test_run_system_reports_current_pipeline_quality_and_safety():
    result = run_system(
        load_cases(CASES_PATH),
        load_snapshot(SNAPSHOT_PATH),
        RecordingReranker(),
        name="identity-reranker",
    )

    assert result.exact_total == 3
    assert result.advisory_positive_count == 17
    assert result.correct_abstentions + result.false_positives == 20
    assert result.degraded_count == 0


def test_gate_rejects_forbidden_or_exact_failure():
    baseline = EvaluationResult(
        name="bm25",
        exact_correct=1,
        exact_total=1,
        advisory_top1=1,
        advisory_recall_at_3=1,
        advisory_mrr_sum=1.0,
        advisory_positive_count=1,
        false_positives=0,
        forbidden_acceptances=0,
        exact_failures=0,
        correct_abstentions=1,
        degraded_count=0,
    )
    unsafe = EvaluationResult(
        **{**baseline.__dict__, "name": "unsafe", "forbidden_acceptances": 1},
    )

    assert passes_gate(baseline, baseline=baseline) is True
    assert passes_gate(unsafe, baseline=baseline) is False


def test_selection_gate_allows_a_small_justified_tradeoff_but_rejects_material_regression():
    baseline = EvaluationResult("bm25", 0, 0, 16, 20, 16.0, 20, 0, 0, 0, 2, 0)
    small_tradeoff = EvaluationResult("small", 0, 0, 15, 20, 17.0, 20, 0, 0, 0, 2, 0)
    material_drop = EvaluationResult("drop", 0, 0, 14, 20, 14.0, 20, 0, 0, 0, 2, 0)

    assert evaluate_selection_gate(small_tradeoff, baseline=baseline, p95_ms=10, peak_rss_mb=10).status == "selected"
    assert evaluate_selection_gate(material_drop, baseline=baseline, p95_ms=10, peak_rss_mb=10).status == "rejected"


def test_model_specs_pin_the_offline_minilm_and_mixedbread_contenders():
    specs = load_model_specs(MODEL_SPECS_PATH)

    assert specs["minilm"]["repo_id"] == "cross-encoder/ms-marco-MiniLM-L6-v2"
    assert specs["mixedbread_xsmall"]["repo_id"] == "mixedbread-ai/mxbai-rerank-xsmall-v1"
    assert specs["mixedbread_xsmall"]["revision"] == "d8e18fdfcfc8b37c036c5c23e9fa9bda8d738cc9"
    assert specs["mixedbread_xsmall"]["onnx_file"] == "onnx/model_quantized.onnx"


def test_bm25_baseline_preserves_the_candidate_order():
    candidates = (
        EvidenceCandidate("first", "first-example", "first evidence", bm25_score=2.0),
        EvidenceCandidate("second", "second-example", "second evidence", bm25_score=1.0),
    )

    assert IdentityReranker().rerank("irrelevant query", candidates) == candidates


def test_reranker_comparisons_receive_the_same_bm25_candidates():
    case = next(case for case in load_cases(CASES_PATH) if case.case_id == "image_pull_advisory")
    first, second = RecordingReranker(), RecordingReranker()

    run_system((case,), load_snapshot(SNAPSHOT_PATH), first, name="minilm")
    run_system((case,), load_snapshot(SNAPSHOT_PATH), second, name="mixedbread_xsmall")

    assert first.candidate_keys == second.candidate_keys
    assert 1 <= len(first.candidate_keys) <= 8


def test_fixture_covers_exact_advisory_and_abstention_across_splits():
    cases = load_cases(CASES_PATH)
    case_ids = {case.case_id for case in cases}

    assert {case.expected_mode for case in cases} == {"exact", "nearest", "none"}
    assert {case.split for case in cases} == {"calibration", "held_out"}
    assert {"oom_exact", "image_pull_advisory", "disk_no_match"}.issubset(case_ids)


def test_fixture_preserves_the_archived_tournament_case_balance():
    cases = load_cases(CASES_PATH)

    assert len(cases) == 40
    assert sum(case.expected_mode != "none" for case in cases) == 20
    assert sum(case.expected_mode == "none" for case in cases) == 20
    assert sum(case.split == "held_out" and case.expected_mode == "none" for case in cases) == 10
    assert sum(case.split == "held_out" and case.expected_mode != "none" for case in cases) == 10


def test_historical_context_enriched_snapshot_has_multiple_examples_without_held_out_reuse():
    cases = load_cases(CASES_PATH)
    snapshot = load_snapshot(SNAPSHOT_PATH)
    held_out_fingerprints = {
        build_template(case).fingerprint()
        for case in cases if case.split == "held_out"
    }

    assert len(snapshot["examples"]) > 16
    assert any(example["example_id"].startswith("historical-") for example in snapshot["examples"])
    assert not any(
        example["exact_reusable"] and example["fingerprint"] in held_out_fingerprints
        for example in snapshot["examples"]
    )


def test_semantic_cases_surface_their_keyword_sharing_competitors_in_bm25():
    snapshot = load_snapshot(SNAPSHOT_PATH)
    for case in load_cases(SEMANTIC_CASES_PATH):
        result = retrieve_case(case, snapshot, IdentityReranker())
        keys = {candidate.knowledge_key for candidate in result.candidates}
        assert keys.intersection(case.expected_keys)
        assert keys.intersection(case.competing_keys)


def test_semantic_fixture_has_a_pre_scoring_ambiguity_validator():
    validate_semantic_cases(load_cases(SEMANTIC_CASES_PATH), load_snapshot(SNAPSHOT_PATH))


def test_semantic_fixture_declares_a_large_enough_provenanced_ambiguity_set():
    """Semantic claims need enough labelled, inspectable cases to be credible."""
    raw_cases = json.loads(SEMANTIC_CASES_PATH.read_text(encoding="utf-8"))

    assert len(raw_cases) >= 16
    assert sum(case.get("provenance") == "archive_derived" for case in raw_cases) >= 8
    for case in raw_cases:
        assert case.get("provenance") in {"archive_derived", "synthetic_stress"}
        assert case.get("competing_keys")
        assert case.get("shared_keywords")
        assert case.get("decisive_fields")
        assert case.get("rationale")


def test_field_coverage_reports_normalized_schema_population():
    coverage = field_coverage(load_cases(SEMANTIC_CASES_PATH))

    assert coverage["triggering_metric"] == 1.0
    assert 0 < coverage["log_error"] < 1.0
    assert 0 < coverage["configuration_diff"] < 1.0
    assert 0 < coverage["trace_error_message"] < 1.0


def test_evidence_category_coverage_is_available_for_dataset_diversity_audits():
    cases = (*load_cases(CASES_PATH), *load_cases(SEMANTIC_CASES_PATH))

    coverage = benchmark.evidence_category_coverage(cases)

    assert set(coverage) == {
        "kubernetes_heavy", "metrics_plus_logs", "logs_plus_traces",
        "configuration_related", "sparse_no_match", "conflicting_evidence",
    }
    assert all(count > 0 for count in coverage.values())


def test_score_floor_turns_rejected_nearest_candidate_into_an_abstention():
    no_match = next(case for case in load_cases(CASES_PATH) if case.case_id == "tls_no_match")

    result = run_system(
        (no_match,),
        load_snapshot(SNAPSHOT_PATH),
        IdentityReranker(),
        name="bm25",
        score_floor=float("inf"),
    )

    assert result.false_positives == 0
    assert result.correct_abstentions == 1


def test_raw_ranking_scores_the_unfiltered_bm25_candidate_order():
    case = next(case for case in load_cases(CASES_PATH) if case.case_id == "image_pull_advisory")
    snapshot = load_snapshot(SNAPSHOT_PATH)

    raw = run_raw_ranking((case,), snapshot, IdentityReranker(), name="bm25")
    accepted = run_system((case,), snapshot, IdentityReranker(), name="bm25", score_floor=float("inf"))

    assert raw.advisory_top1 == 1
    assert accepted.advisory_top1 == 0


def test_bm25_calibration_uses_lexical_scores_instead_of_default_reranker_scores():
    floor = calibrate_score_floor(
        load_cases(CASES_PATH),
        load_snapshot(SNAPSHOT_PATH),
        IdentityReranker(),
        name="bm25",
    )

    assert floor != float("inf")


def test_added_oom_case_uses_an_exact_clean_template_match():
    oom_case = next(case for case in load_cases(CASES_PATH) if case.case_id == "oom_exact")

    result = retrieve_case(oom_case, load_snapshot(SNAPSHOT_PATH), FailIfCalled())

    assert result.mode is EvidenceRetrievalMode.EXACT
    assert result.candidates[0].knowledge_key == "oom"


def test_generic_failure_words_do_not_send_oom_guidance_for_config_error():
    case = next(case for case in load_cases(CASES_PATH) if case.case_id == "generic_crashloop")

    result = retrieve_case(case, load_snapshot(SNAPSHOT_PATH), RecordingReranker())

    assert result.mode is EvidenceRetrievalMode.NEAREST
    assert "oom" not in [candidate.knowledge_key for candidate in result.candidates]


def test_notebook_starts_with_the_current_clean_retrieval_contract():
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    source = "\n".join("".join(cell.get("source", [])) for cell in notebook["cells"])

    assert "fingerprint" in source
    assert "approved hints" in source
    assert "exact / nearest / none / degraded" in source
    assert "LLM judge is intentionally excluded" in source
    assert "git clone https://github.com/Ama2352/vroom-services.git" in source


def test_notebook_contains_pinned_local_models_and_decision_outputs():
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    source = "\n".join("".join(cell.get("source", [])) for cell in notebook["cells"])

    assert "snapshot_download" in source
    assert "verify_sha256" in source
    assert "MiniLMReranker" in source
    assert "Quality metrics" in source
    assert "Safety gate" in source
    assert "Operational metrics" in source
    assert "resource_probe.py" in source
    assert "BM25 remains a comparison baseline" in source
    assert "held-out positive cases" in source
    assert "historical_context_enriched_snapshot.json" in source
    assert "BM25 → MiniLM" in source
    assert "confusion matrix is intentionally omitted" in source


def test_notebook_reports_provenance_coverage_and_pre_scoring_validation():
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    source = "\n".join("".join(cell.get("source", [])) for cell in notebook["cells"])

    assert "validate_semantic_cases" in source
    assert "evidence_category_coverage" in source
    assert "archive_derived" in source
    assert "synthetic_stress" in source
    assert "run_raw_ranking" in source
    assert "Raw Top-8 ranking quality" in source
    assert "complete pipeline" in source


def test_benchmark_execution_cell_imports_numpy_for_its_calibration_guard():
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    source = next(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]
        if "bm25_floor = calibrate_score_floor" in "".join(cell.get("source", []))
    )

    assert "import numpy as np" in source
