from pathlib import Path

from evaluation.baseline import seed_store
from evaluation.bm25_variants import generate_bm25_candidates
from evaluation.fixture_loader import load_cases
from evaluation.models import RankedCandidate, RetrievalCase, VariantConfig
from evaluation.serialization import serialize_candidate, serialize_incident


FIXTURE_PATH = Path(__file__).parents[1] / "evaluation/fixtures/retrieval_cases.json"


def test_candidate_generator_returns_at_most_eight_unthresholded_candidates():
    case = next(case for case in load_cases(FIXTURE_PATH) if case.id == "dns_no_match")
    outcome = generate_bm25_candidates(
        seed_store(), case, VariantConfig("rich_joined", "rich", "joined"), limit=8
    )
    assert len(outcome.candidates) <= 8
    assert all(candidate.score > 0 for candidate in outcome.candidates)


def test_incident_serialization_is_field_labelled_and_stable():
    case = RetrievalCase(
        id="test",
        split="calibration",
        alert_name="PodUnavailable",
        facts={
            "waiting_reason": "CrashLoopBackOff",
            "log_error": "lookup redis: no such host",
        },
        expected_keys=(),
        expected_mode="none",
        forbidden_keys=(),
        rationale="test fixture",
    )
    assert serialize_incident(case) == (
        "alert_name: PodUnavailable\n"
        "waiting_reason: CrashLoopBackOff\n"
        "log_error: lookup redis: no such host"
    )


def test_incident_serialization_includes_template_change_fields_in_stable_order():
    case = RetrievalCase(
        id="test",
        split="calibration",
        alert_name="PodUnavailable",
        facts={
            "dependency": {"name": "redis", "pods_available": 0, "pods_desired": 1},
            "template_diff": {
                "env_diff": [{"key": "REDIS_ADDR", "old_value": "redis:6379", "new_value": "bad-host:6379"}],
                "old_image": "ride:v1",
                "new_image": "ride:v2",
            },
        },
        expected_keys=(),
        expected_mode="none",
        forbidden_keys=(),
        rationale="test fixture",
    )
    assert serialize_incident(case) == (
        "alert_name: PodUnavailable\n"
        "dependency_name: redis\n"
        "dependency_pods_available: 0\n"
        "dependency_pods_desired: 1\n"
        "template_env_key: REDIS_ADDR\n"
        "template_env_old_value: redis:6379\n"
        "template_env_new_value: bad-host:6379\n"
        "template_old_image: ride:v1\n"
        "template_new_image: ride:v2"
    )


def test_candidate_serialization_uses_the_scored_document_text():
    candidate = RankedCandidate(
        "crashloop", 4.2, "knowledge", "crashloop", (), "cause", "fix",
        document_text="CrashLoopBackOff application exits during startup",
    )
    serialized = serialize_candidate(candidate)
    assert "knowledge_key: crashloop" in serialized
    assert "document: CrashLoopBackOff application exits during startup" in serialized
