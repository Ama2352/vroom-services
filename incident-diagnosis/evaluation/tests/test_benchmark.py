import json
from pathlib import Path
import sys


EVALUATION_ROOT = Path(__file__).parents[1]
AGENT_ROOT = EVALUATION_ROOT.parent / "agent"
sys.path.insert(0, str(AGENT_ROOT))
sys.path.insert(0, str(EVALUATION_ROOT))

from benchmark import (
    EvaluationResult,
    IdentityReranker,
    build_template,
    load_cases,
    load_model_specs,
    load_snapshot,
    passes_gate,
    retrieve_case,
    run_system,
)
from retrieval.models import EvidenceCandidate, EvidenceRetrievalMode


CASES_PATH = EVALUATION_ROOT / "fixtures" / "retrieval_cases.json"
SNAPSHOT_PATH = EVALUATION_ROOT / "fixtures" / "knowledge_snapshot.json"
MODEL_SPECS_PATH = EVALUATION_ROOT / "model_specs.json"
RUNTIME_MINILM_MANIFEST = AGENT_ROOT / "retrieval" / "model_manifest.json"
NOTEBOOK_PATH = EVALUATION_ROOT / "model_selection_colab.ipynb"
README_PATH = EVALUATION_ROOT / "README.md"


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
    assert result.candidates[0].knowledge_key == "redis_connection"


def test_nearest_case_reranks_hint_backed_bm25_candidates():
    case = next(case for case in load_cases(CASES_PATH) if case.expected_mode == "nearest")
    reranker = RecordingReranker()

    result = retrieve_case(case, load_snapshot(SNAPSHOT_PATH), reranker)

    assert result.mode is EvidenceRetrievalMode.NEAREST
    assert result.candidates[0].knowledge_key == "image_pull"
    assert any("approved_hints: verify the image tag" in text for text in reranker.candidate_texts)


def test_unsupported_case_abstains_without_reranking_generic_terms():
    case = next(case for case in load_cases(CASES_PATH) if case.expected_mode == "none")

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

    assert result.exact_correct == 2
    assert result.exact_total == 2
    assert result.advisory_positive_count == 3
    assert result.advisory_top1 == 3
    assert result.advisory_recall_at_3 == 3
    assert result.advisory_mrr_sum == 3.0
    assert result.correct_abstentions == 2
    assert result.false_positives == 0
    assert result.forbidden_acceptances == 0
    assert result.exact_failures == 0
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


def test_model_specs_pin_the_runtime_minilm_and_mixedbread_contender():
    specs = load_model_specs(MODEL_SPECS_PATH)
    runtime_minilm = json.loads(RUNTIME_MINILM_MANIFEST.read_text(encoding="utf-8"))

    assert specs["minilm"] == runtime_minilm
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
    case = next(case for case in load_cases(CASES_PATH) if case.case_id == "config_error_nearest")
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
    assert {"oom_exact", "config_error_nearest", "disk_pressure_none"}.issubset(case_ids)


def test_added_oom_case_uses_an_exact_clean_template_match():
    oom_case = next(case for case in load_cases(CASES_PATH) if case.case_id == "oom_exact")

    result = retrieve_case(oom_case, load_snapshot(SNAPSHOT_PATH), FailIfCalled())

    assert result.mode is EvidenceRetrievalMode.EXACT
    assert result.candidates[0].knowledge_key == "oom_killed"


def test_generic_failure_words_do_not_send_oom_guidance_for_config_error():
    case = next(case for case in load_cases(CASES_PATH) if case.case_id == "config_error_nearest")

    result = retrieve_case(case, load_snapshot(SNAPSHOT_PATH), RecordingReranker())

    assert result.mode is EvidenceRetrievalMode.NEAREST
    assert [candidate.knowledge_key for candidate in result.candidates] == ["config_error"]


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
    assert "confusion matrix is intentionally omitted" in source


def test_readme_explains_colab_without_production_access():
    source = README_PATH.read_text(encoding="utf-8")

    assert "Google Colab" in source
    assert "API key" in source
    assert "Kubernetes" in source
    assert "Redis" in source
    assert "CPU" in source
