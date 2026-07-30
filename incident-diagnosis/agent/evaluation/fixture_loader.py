import json
from pathlib import Path

from evaluation.models import RetrievalCase


_SPLITS = {"calibration", "held_out"}
_MODES = {"exact", "advisory", "none"}


def load_cases(path: Path) -> tuple[RetrievalCase, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("fixture root must be a JSON array")

    cases = []
    seen = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"case {index} must be an object")
        case_id = item.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"case {index} id must be a non-empty string")
        if case_id in seen:
            raise ValueError(f"duplicate case id: {case_id}")
        seen.add(case_id)

        split = item.get("split")
        mode = item.get("expected_mode")
        if split not in _SPLITS:
            raise ValueError(f"{case_id}: split must be calibration or held_out")
        if mode not in _MODES:
            raise ValueError(f"{case_id}: expected_mode must be exact, advisory, or none")

        expected_keys = tuple(item.get("expected_keys") or ())
        forbidden_keys = tuple(item.get("forbidden_keys") or ())
        if mode == "none" and expected_keys:
            raise ValueError(f"{case_id}: expected_keys must be empty for mode none")
        if mode != "none" and not expected_keys:
            raise ValueError(f"{case_id}: expected_keys must not be empty")

        cases.append(RetrievalCase(
            id=case_id,
            split=split,
            alert_name=str(item.get("alert_name") or ""),
            facts=dict(item.get("facts") or {}),
            expected_keys=expected_keys,
            expected_mode=mode,
            forbidden_keys=forbidden_keys,
            rationale=str(item.get("rationale") or ""),
        ))
    return tuple(cases)
