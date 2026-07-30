import json
from pathlib import Path

import pytest

from evaluation.fixture_loader import load_cases


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "evaluation"
    / "fixtures"
    / "retrieval_cases.json"
)


def test_catalog_has_balanced_frozen_splits():
    cases = load_cases(FIXTURE_PATH)
    assert len(cases) == 20
    assert sum(c.split == "calibration" for c in cases) == 10
    assert sum(c.split == "held_out" for c in cases) == 10
    assert len({c.id for c in cases}) == len(cases)


def test_catalog_contains_positive_negative_and_ambiguous_cases():
    cases = load_cases(FIXTURE_PATH)
    assert any(c.expected_mode == "exact" for c in cases)
    assert any(c.expected_mode == "advisory" for c in cases)
    assert any(c.expected_mode == "none" for c in cases)
    assert any(len(c.expected_keys) > 1 for c in cases)
    assert any(c.forbidden_keys for c in cases)


def test_loader_rejects_expected_keys_for_no_match(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{
        "id": "bad",
        "split": "calibration",
        "alert_name": "A",
        "facts": {},
        "expected_keys": ["oom"],
        "expected_mode": "none",
        "forbidden_keys": [],
        "rationale": "invalid",
    }]))
    with pytest.raises(ValueError, match="expected_keys must be empty"):
        load_cases(path)


def test_loader_rejects_unknown_split(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{
        "id": "bad",
        "split": "training",
        "alert_name": "A",
        "facts": {},
        "expected_keys": [],
        "expected_mode": "none",
        "forbidden_keys": [],
        "rationale": "invalid",
    }]))
    with pytest.raises(ValueError, match="split"):
        load_cases(path)
