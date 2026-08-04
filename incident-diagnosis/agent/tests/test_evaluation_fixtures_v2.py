from dataclasses import replace
from pathlib import Path

import pytest

from evaluation.fixture_loader import load_cases, validate_tournament_catalog


FIXTURE = Path(__file__).parents[1] / "evaluation/fixtures/retrieval_cases_v2.json"


def test_v2_catalog_has_required_balance_and_frozen_negative_set():
    cases = load_cases(FIXTURE)
    validate_tournament_catalog(cases)
    positives = [case for case in cases if case.expected_mode != "none"]
    negatives = [case for case in cases if case.expected_mode == "none"]
    held_negatives = [case for case in negatives if case.split == "held_out"]
    assert len(cases) == 40
    assert len(positives) == 20
    assert len(negatives) == 20
    assert len(held_negatives) == 10


def test_v2_catalog_covers_every_bootstrap_knowledge_key():
    cases = load_cases(FIXTURE)
    represented = {key for case in cases for key in case.expected_keys}
    assert {
        "init_oom", "init_crashloop", "oom", "crashloop", "image_pull",
        "config_error", "failed_scheduling", "zero_replica",
    }.issubset(represented)


def test_v2_catalog_preserves_required_evidence_categories():
    ids = {case.id for case in load_cases(FIXTURE)}
    assert {
        "high_error_rate", "trip_timeout_storm", "argocd_outofsync",
        "kargo_analysis_failed", "sparse_no_match", "ambiguous_conclusive",
    }.issubset(ids)


def test_validator_rejects_catalog_with_too_few_held_out_negatives():
    original = load_cases(FIXTURE)
    target = next(
        case for case in original
        if case.split == "held_out" and case.expected_mode == "none"
    )
    cases = tuple(
        replace(case, split="calibration") if case.id == target.id else case
        for case in original
    )
    with pytest.raises(ValueError, match="held-out no-match"):
        validate_tournament_catalog(cases)
