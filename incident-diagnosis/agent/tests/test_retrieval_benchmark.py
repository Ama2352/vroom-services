import json
import os
from pathlib import Path

import fakeredis
import pytest

import seed
from retrieval.service import create_retrieval_service


FIXTURE = Path(__file__).parent / "fixtures" / "retrieval_cases_v2.json"
MODEL_DIR = os.environ.get("RERANKER_MODEL_DIR")


@pytest.mark.model
def test_minilm_fixture_gate_prioritizes_precision():
    if not MODEL_DIR or not Path(MODEL_DIR).is_dir():
        pytest.skip("set RERANKER_MODEL_DIR to run the pinned MiniLM benchmark")
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    redis = fakeredis.FakeRedis()
    seed.seed_if_empty(redis, str(Path(__file__).parents[1] / "runbooks"))
    service = create_retrieval_service(redis, Path(MODEL_DIR))

    for split in ("calibration", "held_out"):
        split_cases = [case for case in cases if case["split"] == split]
        positive = [case for case in split_cases if case["expected_keys"]]
        true_positive = false_positive = 0
        for case in split_cases:
            result = service.retrieve(case["alert_name"], case["facts"])
            key = result.candidate.knowledge_key if result.candidate else None
            accepted = key is not None
            if accepted and (key in case["expected_keys"] and key not in case["forbidden_keys"]):
                true_positive += 1
            elif accepted:
                false_positive += 1
        assert false_positive == 0
        assert true_positive / len(positive) >= 0.60
