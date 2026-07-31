from dataclasses import replace
from pathlib import Path

import pytest

from evaluation.fixture_loader import load_cases
from evaluation.models import RetrievalOutcome
from evaluation.resource_probe import measure_local_adapter, nearest_rank_percentile
from evaluation.tournament import parse_cli_args, run_tournament
from evaluation.tournament_models import DecisionTrace, OperationalMetrics


FIXTURE = Path(__file__).parents[1] / "evaluation/fixtures/retrieval_cases_v2.json"


@pytest.fixture
def v2_cases():
    return load_cases(FIXTURE)


class FakeLocalAdapter:
    def __init__(self, *, unstable=False):
        self.events = []
        self.held_out_calls = 0
        self.held_out_calls_by_case = {}
        self.unstable = unstable
        self.operational = OperationalMetrics(p95_ms=5.0, peak_rss_mb=50.0)

    def evaluate(self, batch, *, floor):
        case, candidates = batch
        self.events.append(case.split)
        if case.split == "held_out":
            self.held_out_calls += 1
            self.held_out_calls_by_case[case.id] = (
                self.held_out_calls_by_case.get(case.id, 0) + 1
            )
        outcome = candidates
        if self.unstable and self.held_out_calls_by_case.get(case.id) == 2:
            outcome = RetrievalOutcome("none", (), candidates.exact_ambiguous)
        return DecisionTrace(outcome)

    def freeze(self, name, floor):
        self.events.append(f"freeze:{name}")


class FakeLlmAdapter:
    provider = "fake"
    model = "fake-judge"

    def __init__(self):
        self.events = []
        self.held_out_calls = 0

    def evaluate(self, batch):
        case, candidates = batch
        self.events.append(case.split)
        if case.split == "held_out":
            self.held_out_calls += 1
        return DecisionTrace(candidates, latency_ms=2.0)

    def freeze(self, name, value):
        self.events.append(f"freeze:{name}")


@pytest.fixture
def fake_local_adapter():
    return FakeLocalAdapter()


@pytest.fixture
def fake_llm_adapter():
    return FakeLlmAdapter()


@pytest.fixture
def fake_adapters():
    return {
        "minilm": FakeLocalAdapter(),
        "mixedbread_xsmall": FakeLocalAdapter(),
    }


def test_all_challengers_receive_identical_candidate_ids(fake_adapters, v2_cases):
    result = run_tournament(v2_cases, adapters=fake_adapters, include_llm=False)
    observed = result["debug"]["candidate_ids_by_system"]
    assert observed["bm25"] == observed["minilm"] == observed["mixedbread_xsmall"]


def test_thresholds_are_frozen_before_any_held_out_adapter_call(v2_cases):
    spy_adapter = FakeLocalAdapter()
    run_tournament(v2_cases, adapters={"minilm": spy_adapter}, include_llm=False)
    first_held = spy_adapter.events.index("held_out")
    assert "freeze:minilm" in spy_adapter.events[:first_held]


def test_all_local_floors_freeze_before_first_held_out_call(v2_cases):
    events = []

    class OrderedAdapter(FakeLocalAdapter):
        def evaluate(self, batch, *, floor):
            events.append(f"call:{self.name}:{batch[0].split}")
            return super().evaluate(batch, floor=floor)

        def freeze(self, name, floor):
            events.append(f"freeze:{name}")
            super().freeze(name, floor)

    minilm = OrderedAdapter()
    minilm.name = "minilm"
    mixedbread = OrderedAdapter()
    mixedbread.name = "mixedbread_xsmall"
    run_tournament(
        v2_cases,
        adapters={"minilm": minilm, "mixedbread_xsmall": mixedbread},
    )
    first_held = next(index for index, event in enumerate(events) if event.endswith("held_out"))
    assert "freeze:minilm" in events[:first_held]
    assert "freeze:mixedbread_xsmall" in events[:first_held]


def test_llm_runs_held_out_three_times(fake_llm_adapter, v2_cases):
    run_tournament(v2_cases, adapters={"llm": fake_llm_adapter}, include_llm=True)
    held_count = sum(case.split == "held_out" for case in v2_cases)
    assert fake_llm_adapter.held_out_calls == held_count * 3


def test_local_systems_repeat_held_out_and_require_identical_results(
    fake_local_adapter, v2_cases
):
    result = run_tournament(v2_cases, adapters={"minilm": fake_local_adapter})
    held_count = sum(case.split == "held_out" for case in v2_cases)
    assert fake_local_adapter.held_out_calls == held_count * 2
    assert result["systems"]["minilm"]["stable"] is True


def test_local_stability_compares_ordered_outcomes(v2_cases):
    result = run_tournament(
        v2_cases, adapters={"minilm": FakeLocalAdapter(unstable=True)}
    )
    assert result["systems"]["minilm"]["stable"] is False


def test_invalid_fixture_contract_returns_incomplete(v2_cases):
    duplicate = replace(v2_cases[-1], id=v2_cases[0].id)
    result = run_tournament((*v2_cases[:-1], duplicate), adapters={})
    assert result["decision"] == "INCOMPLETE"
    assert result["failure_reasons"][0]["phase"] == "fixture_validation"


def test_nearest_rank_percentile_uses_ceiling_rank():
    assert nearest_rank_percentile((9.0, 1.0, 5.0, 2.0), 0.50) == 2.0
    assert nearest_rank_percentile((9.0, 1.0, 5.0, 2.0), 0.95) == 9.0
    with pytest.raises(ValueError, match="at least one"):
        nearest_rank_percentile((), 0.95)


def test_measurement_uses_injected_probe_and_serializes_metrics(tmp_path):
    artifact = tmp_path / "model.onnx"
    artifact.write_bytes(b"1234")

    def probe(request):
        assert request["repetitions"] == 2
        return {
            "available": True,
            "cold_load_ms": 7.0,
            "latencies_ms": [1.0, 2.0, 8.0],
            "peak_rss_mb": 42.0,
            "rss_samples_mb": [40.0, 41.0, 42.0],
            "runs": [["first"], ["second"]],
        }

    result = measure_local_adapter(
        name="fake",
        artifact_path=artifact,
        model_dir=tmp_path,
        spec=None,
        batches=(),
        floor=0.0,
        repetitions=2,
        process_probe=probe,
        dependency_size_probe=lambda: 6.0,
    )
    assert result["available"] is True
    assert result["artifact_mb"] == pytest.approx(4 / (1024 * 1024))
    assert result["estimated_container_delta_mb"] == pytest.approx(
        6.0 + 4 / (1024 * 1024)
    )
    assert result["container_delta_is_estimate"] is True
    assert result["p50_ms"] == 2.0
    assert result["p95_ms"] == 8.0
    assert result["rss_samples_mb"] == [40.0, 41.0, 42.0]
    assert result["runs"] == [["first"], ["second"]]


def test_measurement_worker_error_marks_only_system_unavailable(tmp_path):
    artifact = tmp_path / "model.onnx"
    artifact.write_bytes(b"x")

    def broken_probe(request):
        raise RuntimeError("worker exploded")

    result = measure_local_adapter(
        name="fake",
        artifact_path=artifact,
        model_dir=tmp_path,
        spec=None,
        batches=(),
        floor=0.0,
        process_probe=broken_probe,
        dependency_size_probe=lambda: 0.0,
    )
    assert result["available"] is False
    assert result["error"]["type"] == "RuntimeError"
    assert "worker exploded" in result["error"]["message"]


def test_cli_defaults_do_not_prepare_models_or_enable_llm():
    args = parse_cli_args([])
    assert args.prepare_models is False
    assert args.include_llm is False
    assert args.llm_input_usd_per_million == 0.0
    assert args.llm_output_usd_per_million == 0.0


def test_missing_llm_key_is_structured_unavailable(monkeypatch, v2_cases):
    monkeypatch.delenv("GROQ_KEY", raising=False)
    result = run_tournament(v2_cases, adapters={}, include_llm=True)
    assert result["systems"]["llm"]["status"] == "unavailable"
    assert "GROQ_KEY" in result["systems"]["llm"]["failure_reasons"][0]


def test_prompt_revision_hook_receives_calibration_only(fake_llm_adapter, v2_cases):
    observed = []

    def revise(cases, traces):
        observed.extend(case.split for case in cases)
        assert set(traces) == {case.id for case in cases}

    run_tournament(
        v2_cases,
        adapters={"llm": fake_llm_adapter},
        include_llm=True,
        prompt_revision_hook=revise,
    )
    assert observed and set(observed) == {"calibration"}


def test_bm25_control_cannot_create_semantic_pass(v2_cases):
    result = run_tournament(v2_cases, adapters={}, include_llm=False)
    assert result["recommendation"] is None
    assert result["decision"] == "FAIL"
