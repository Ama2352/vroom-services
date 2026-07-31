import hashlib
import json

import pytest
import requests

from evaluation.models import RankedCandidate, RetrievalCase, RetrievalOutcome
from evaluation.llm_judge import (
    GroqJudgeClient,
    aggregate_repetitions,
    judge_candidates,
    parse_judgment,
)


@pytest.fixture
def candidate_batch():
    case = RetrievalCase(
        "dns", "held_out", "PodUnavailable",
        {"waiting_reason": "CrashLoopBackOff", "log_error": "lookup redis: no such host"},
        (), "none", (), "specific DNS failure must not be explained generically",
    )
    candidates = (
        RankedCandidate("crashloop", 9.0, "knowledge", "crashloop", (), "application exits", "inspect logs", document_text="generic restart pattern"),
        RankedCandidate("dns", 7.0, "knowledge", "dns", (), "DNS lookup failure", "check name server", document_text="lookup failure pattern"),
    )
    return case, RetrievalOutcome("advisory", candidates)


def _accepted(key="dns", relevance=3, conflicts=()):
    return json.dumps({
        "decision": "accepted",
        "selected_keys": [key],
        "evaluations": [
            {"key": "crashloop", "supported": False, "relevance": 0, "supporting_fields": [], "conflicting_fields": [], "reason": "generic state leaves DNS unexplained"},
            {"key": "dns", "supported": key == "dns", "relevance": relevance if key == "dns" else 0, "supporting_fields": ["log_error"] if key == "dns" else [], "conflicting_fields": list(conflicts) if key == "dns" else [], "reason": "DNS error is specific"},
        ],
    })


def test_parser_accepts_reject_all_and_preserves_conflicts(candidate_batch):
    raw = json.dumps({
        "decision": "no_supported_candidate",
        "selected_keys": [],
        "evaluations": [{
            "key": "crashloop", "supported": False, "relevance": 1,
            "supporting_fields": ["waiting_reason"],
            "conflicting_fields": ["log_error"],
            "reason": "Restart state does not explain DNS failure.",
        }, {
            "key": "dns", "supported": False, "relevance": 0,
            "supporting_fields": [], "conflicting_fields": [], "reason": "not selected",
        }],
    })

    trace = parse_judgment(raw, candidate_batch)

    assert trace.outcome.mode == "none"
    assert trace.decisions[0].conflicting_fields == ("log_error",)


def test_parser_rejects_unknown_candidate_key(candidate_batch):
    raw = '{"decision":"accepted","selected_keys":["invented"],"evaluations":[]}'

    with pytest.raises(ValueError, match="unknown candidate"):
        parse_judgment(raw, candidate_batch)


def test_parser_normalizes_explicit_candidate_supported_decision_alias(candidate_batch):
    raw = _accepted().replace('"decision": "accepted"', '"decision": "candidate_2_supported"')

    trace = parse_judgment(raw, candidate_batch)

    assert trace.outcome.mode == "advisory"
    assert [candidate.knowledge_key for candidate in trace.outcome.candidates] == ["dns"]


@pytest.mark.parametrize("raw", [
    '```json\n{"decision":"no_supported_candidate","selected_keys":[],"evaluations":[]}\n```',
    '{"decision":"accepted","decision":"accepted","selected_keys":["dns"],"evaluations":[]}',
    '{"decision":"accepted","selected_keys":[],"evaluations":[]}',
])
def test_parser_rejects_non_schema_or_ambiguous_json(candidate_batch, raw):
    with pytest.raises(ValueError):
        parse_judgment(raw, candidate_batch)


def test_conflicting_selected_candidate_is_abstained_by_application_policy(candidate_batch):
    trace = parse_judgment(_accepted(conflicts=("log_error",)), candidate_batch)

    assert trace.outcome.mode == "none"
    assert trace.decisions[1].accepted is False
    assert trace.decisions[1].conflicting_fields == ("log_error",)


def test_parser_orders_selected_candidates_by_relevance_then_bm25_order(candidate_batch):
    raw = json.dumps({
        "decision": "accepted", "selected_keys": ["dns", "crashloop"],
        "evaluations": [
            {"key": "crashloop", "supported": True, "relevance": 2, "supporting_fields": ["waiting_reason"], "conflicting_fields": [], "reason": "restart state"},
            {"key": "dns", "supported": True, "relevance": 2, "supporting_fields": ["log_error"], "conflicting_fields": [], "reason": "DNS error"},
        ],
    })

    trace = parse_judgment(raw, candidate_batch)

    assert [candidate.knowledge_key for candidate in trace.outcome.candidates] == ["crashloop", "dns"]


class FakeJudgeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def judge(self, prompt):
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response, {"prompt_tokens": 11, "completion_tokens": 7, "http_status": 200}


def test_judge_candidates_repeats_three_times_and_records_prompt_hash(candidate_batch):
    client = FakeJudgeClient([_accepted(), _accepted(), _accepted()])

    result = judge_candidates(candidate_batch, client, repetitions=3)

    assert result.stable is True
    assert result.agreement_count == 3
    assert result.request_count == 3
    assert result.input_tokens == 33
    assert result.output_tokens == 21
    assert result.majority.prompt_sha256 == hashlib.sha256(client.prompts[0].encode("utf-8")).hexdigest()
    assert "INCIDENT_EVIDENCE" in client.prompts[0]
    assert "CANDIDATES" in client.prompts[0]


def test_repetition_disagreement_is_unstable(candidate_batch):
    none = json.dumps({
        "decision": "no_supported_candidate", "selected_keys": [],
        "evaluations": [
            {"key": "crashloop", "supported": False, "relevance": 0, "supporting_fields": [], "conflicting_fields": [], "reason": "not enough"},
            {"key": "dns", "supported": False, "relevance": 0, "supporting_fields": [], "conflicting_fields": [], "reason": "not enough"},
        ],
    })
    traces = tuple(parse_judgment(raw, candidate_batch) for raw in (none, _accepted(), none))

    result = aggregate_repetitions(traces)

    assert result.stable is False
    assert result.majority.outcome.mode == "none"
    assert result.agreement_count == 2


def test_client_posts_only_the_pinned_groq_json_request(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 3}}

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr("evaluation.llm_judge.requests.post", post)
    client = GroqJudgeClient("not-a-real-key", timeout_seconds=12)

    content, metadata = client.judge("payload")

    assert content == "{}"
    assert metadata["prompt_tokens"] == 3
    assert metadata["http_status"] == 200
    assert metadata["latency_ms"] >= 0
    assert calls == [(("https://api.groq.com/openai/v1/chat/completions",), {
        "headers": {"Authorization": "Bearer not-a-real-key"},
        "json": {
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": "payload"}],
            "temperature": 0,
            "max_tokens": 900,
            "response_format": {"type": "json_object"},
        },
        "timeout": 12,
    })]


def test_http_error_trace_retains_response_telemetry(candidate_batch, monkeypatch):
    class Response:
        status_code = 429

        def raise_for_status(self):
            raise requests.HTTPError("rate limited")

        def json(self):
            return {"usage": {"prompt_tokens": 4, "completion_tokens": 2}}

    monkeypatch.setattr("evaluation.llm_judge.requests.post", lambda *args, **kwargs: Response())

    result = judge_candidates(candidate_batch, GroqJudgeClient("not-a-real-key"))

    assert result.stable is False
    assert result.majority.http_status == 429
    assert result.majority.input_tokens == 4
    assert result.majority.output_tokens == 2
    assert result.majority.latency_ms >= 0
    assert result.majority.parse_outcome == "error"


def test_timeout_error_trace_retains_elapsed_telemetry(candidate_batch, monkeypatch):
    monkeypatch.setattr(
        "evaluation.llm_judge.requests.post",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout("timed out")),
    )

    result = judge_candidates(candidate_batch, GroqJudgeClient("not-a-real-key"))

    assert result.stable is False
    assert result.majority.http_status is None
    assert result.majority.latency_ms >= 0
    assert result.majority.parse_outcome == "error"


def test_parse_error_trace_retains_successful_request_telemetry(candidate_batch, monkeypatch):
    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": "not-json"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            }

    monkeypatch.setattr("evaluation.llm_judge.requests.post", lambda *args, **kwargs: Response())

    result = judge_candidates(candidate_batch, GroqJudgeClient("not-a-real-key"))

    assert result.stable is False
    assert result.majority.http_status == 200
    assert result.majority.input_tokens == 5
    assert result.majority.output_tokens == 3
    assert result.majority.parse_outcome == "error"


def test_preflight_returns_parsed_or_errored_telemetry_without_network(monkeypatch):
    class Success:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": json.dumps({
                    "decision": "no_supported_candidate", "selected_keys": [], "evaluations": [],
                })}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            }

    monkeypatch.setattr("evaluation.llm_judge.requests.post", lambda *args, **kwargs: Success())
    success = GroqJudgeClient("not-a-real-key").preflight()
    assert success.error is None
    assert success.http_status == 200
    assert success.parse_outcome == "parsed"

    monkeypatch.setattr(
        "evaluation.llm_judge.requests.post",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout("timed out")),
    )
    failure = GroqJudgeClient("not-a-real-key").preflight()
    assert failure.error is not None
    assert failure.http_status is None
    assert failure.latency_ms >= 0
    assert failure.parse_outcome == "error"


def test_preflight_parse_error_retains_successful_request_telemetry(monkeypatch):
    class InvalidSchema:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": "not-json"}}],
                "usage": {"prompt_tokens": 6, "completion_tokens": 4},
            }

    monkeypatch.setattr("evaluation.llm_judge.requests.post", lambda *args, **kwargs: InvalidSchema())

    result = GroqJudgeClient("not-a-real-key").preflight()

    assert result.error is not None
    assert result.http_status == 200
    assert result.input_tokens == 6
    assert result.output_tokens == 4
    assert result.latency_ms >= 0
    assert result.parse_outcome == "error"
