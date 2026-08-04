from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re

import pytest

from evaluation.fixture_loader import load_cases
from evaluation.models import RankedCandidate, RetrievalOutcome
from evaluation import resource_probe
from evaluation.resource_probe import measure_local_adapter, nearest_rank_percentile
from evaluation.tournament import parse_cli_args, run_tournament
from evaluation.tournament_models import CandidateDecision, DecisionTrace, OperationalMetrics


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


def test_real_judge_adapter_bypasses_exact_without_provider_call(v2_cases):
    exact = next(case for case in v2_cases if case.expected_mode == "exact")

    class NeverCalledJudge:
        def __init__(self):
            self.calls = 0

        def judge(self, prompt):
            self.calls += 1
            raise AssertionError("exact retrieval must bypass the provider")

    from evaluation.tournament import _invoke_llm

    outcome = RetrievalOutcome(
        "exact",
        (RankedCandidate("exact-key", 1.0, "knowledge", "exact-key", (), "cause", "fix"),),
    )
    judge = NeverCalledJudge()
    trace = _invoke_llm(judge, (exact, outcome), "FROZEN TEMPLATE")
    assert trace.outcome is outcome
    assert trace.parse_outcome == "bypassed"
    assert judge.calls == 0


def test_local_systems_repeat_held_out_and_require_identical_results(
    fake_local_adapter, v2_cases
):
    result = run_tournament(v2_cases, adapters={"minilm": fake_local_adapter})
    held_count = sum(case.split == "held_out" for case in v2_cases)
    assert fake_local_adapter.held_out_calls == held_count * 2
    assert result["systems"]["minilm"]["stable"] is True


def test_local_system_serializes_calibration_and_both_held_out_runs(v2_cases, tmp_path):
    class ScoredAdapter(FakeLocalAdapter):
        def evaluate(self, batch, *, floor):
            trace = super().evaluate(batch, floor=floor)
            decisions = tuple(
                CandidateDecision(candidate.knowledge_key, True, 0.75 - index / 10)
                for index, candidate in enumerate(batch[1].candidates)
            )
            return DecisionTrace(trace.outcome, decisions, latency_ms=1.25)

    result = run_tournament(
        v2_cases,
        adapters={"minilm": ScoredAdapter()},
        model_cache=tmp_path / "empty",
    )
    system = result["systems"]["minilm"]
    assert len(system["calibration_traces"]) == 20
    assert len(system["held_out_runs"]) == 2
    assert all(len(run) == 20 for run in system["held_out_runs"])
    scored = next(
        trace for trace in system["calibration_traces"].values()
        if trace["decisions"]
    )
    assert scored["decisions"][0]["score"] == pytest.approx(0.75)
    assert scored["latency_ms"] == pytest.approx(1.25)


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
    model_dir = tmp_path / "model"
    artifact = model_dir / "onnx" / "model.onnx"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"1234")
    (model_dir / "tokenizer.json").write_bytes(b"56")

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
        model_dir=model_dir,
        spec=None,
        batches=(),
        floor=0.0,
        repetitions=2,
        process_probe=probe,
        dependency_size_probe=lambda: 6.0,
    )
    assert result["available"] is True
    assert result["artifact_mb"] == pytest.approx(6 / (1024 * 1024))
    assert result["estimated_container_delta_mb"] == pytest.approx(
        6.0 + 6 / (1024 * 1024)
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


def test_spawn_probe_drains_oversized_payload_before_join(monkeypatch):
    state = {"drained": False}
    payload = {"available": True, "padding": "x" * (4 * 1024 * 1024)}

    class FakeQueue:
        def get(self, timeout):
            state["drained"] = True
            return payload

        def close(self):
            pass

    class FakeProcess:
        exitcode = 0

        def start(self):
            pass

        def join(self, timeout):
            assert state["drained"], "joining before queue drain can deadlock"

        def is_alive(self):
            return False

    class FakeContext:
        def Queue(self):
            return FakeQueue()

        def Process(self, target, args):
            return FakeProcess()

    monkeypatch.setattr(
        resource_probe.multiprocessing, "get_context", lambda method: FakeContext()
    )
    assert resource_probe._spawn_process_probe({"timeout_seconds": 2}) is payload


def test_dependency_footprint_includes_transitive_but_excludes_base_closure(
    monkeypatch, tmp_path
):
    class FakeDistribution:
        def __init__(self, name, requires, size):
            self.requires = requires
            self.files = (Path(f"{name}.bin"),)
            self.path = tmp_path / f"{name}.bin"
            self.path.write_bytes(b"x" * size)

        def locate_file(self, relative):
            return self.path

    distributions = {
        "eval-root": FakeDistribution(
            "eval-root", ["transitive>=1", "base-shared>=1"], 2
        ),
        "transitive": FakeDistribution("transitive", [], 3),
        "base": FakeDistribution("base", ["base-shared>=1"], 7),
        "base-shared": FakeDistribution("base-shared", [], 5),
    }
    monkeypatch.setattr(
        resource_probe.importlib.metadata,
        "distribution",
        lambda name: distributions[name],
    )
    measured = resource_probe._distribution_size_mb(
        evaluation_roots=("eval-root",), base_roots=("base",)
    )
    assert measured == pytest.approx(5 / (1024 * 1024))


def test_cli_defaults_do_not_prepare_models_or_enable_llm():
    args = parse_cli_args([])
    assert args.prepare_models is False
    assert args.include_llm is False
    assert args.llm_input_usd_per_million == 0.0
    assert args.llm_output_usd_per_million == 0.0


def test_missing_llm_key_is_structured_unavailable(monkeypatch, v2_cases, tmp_path):
    monkeypatch.delenv("GROQ_KEY", raising=False)
    result = run_tournament(
        v2_cases, adapters={}, include_llm=True, model_cache=tmp_path / "empty"
    )
    assert result["systems"]["llm"]["status"] == "unavailable"
    assert "GROQ_KEY" in result["systems"]["llm"]["failure_reasons"][0]
    assert result["systems"]["llm"]["calibration"] is None
    assert result["systems"]["llm"]["held_out"] is None


def test_run_metadata_records_pricing_counts_and_exact_reproduction(fake_adapters):
    result = run_tournament(
        FIXTURE,
        adapters=fake_adapters,
        include_llm=True,
        model_cache=Path("evaluation/.models"),
        report_dir=Path("evaluation/reports"),
        llm_input_usd_per_million=0.05,
        llm_output_usd_per_million=0.08,
        pricing_source_url="https://groq.com/pricing",
        pricing_retrieved_at="2026-07-31",
    )
    assert result["dataset"]["held_out_no_match_cases"] == 10
    assert result["environment"]["pricing_snapshot"] == {
        "source_url": "https://groq.com/pricing",
        "retrieved_at": "2026-07-31",
        "provider": "groq",
        "model_id": "llama-3.1-8b-instant",
        "input_usd_per_million": 0.05,
        "output_usd_per_million": 0.08,
    }
    command = result["reproduction"]["command"]
    for fragment in (
        "--fixtures", "--report-dir evaluation/reports",
        "--model-cache evaluation/.models", "--include-llm",
        "--llm-input-usd-per-million 0.05",
        "--llm-output-usd-per-million 0.08",
        "--pricing-source-url https://groq.com/pricing",
        "--pricing-retrieved-at 2026-07-31",
    ):
        assert fragment in command


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


def test_held_out_judge_uses_frozen_revised_prompt_and_hash(v2_cases):
    class RecordingJudge:
        provider = "fake-provider"
        model = "fake-model"

        def __init__(self):
            self.prompts = []
            self.frozen_at = None
            self.frozen = None

        def judge(self, prompt):
            self.prompts.append(prompt)
            keys = re.findall(r"^knowledge_key: (.+)$", prompt, re.MULTILINE)
            payload = {
                "decision": "no_supported_candidate",
                "selected_keys": [],
                "evaluations": [
                    {
                        "key": key,
                        "supported": False,
                        "relevance": 0,
                        "supporting_fields": [],
                        "conflicting_fields": [],
                        "reason": "not supported",
                    }
                    for key in keys
                ],
            }
            return json.dumps(payload), {"latency_ms": 1.0}

        def freeze(self, name, frozen):
            self.frozen_at = len(self.prompts)
            self.frozen = frozen

    judge = RecordingJudge()
    revised = "REVISED FROZEN RETRIEVAL TEMPLATE"
    result = run_tournament(
        v2_cases,
        adapters={"llm": judge},
        include_llm=True,
        prompt_revision_hook=lambda cases, traces: revised,
    )
    held_prompts = judge.prompts[judge.frozen_at :]
    assert held_prompts and all(prompt.startswith(revised) for prompt in held_prompts)
    assert judge.frozen["prompt_text"] == revised
    assert judge.frozen["prompt_sha256"] == hashlib.sha256(revised.encode()).hexdigest()
    assert result["systems"]["llm"]["frozen_identity"] == judge.frozen
    assert result["environment"]["prompt"]["text"] == revised


def test_bm25_control_cannot_create_semantic_pass(v2_cases, tmp_path):
    result = run_tournament(
        v2_cases, adapters={}, include_llm=False, model_cache=tmp_path / "empty"
    )
    assert result["recommendation"] is None
    assert result["decision"] == "FAIL"
