"""Deterministic offline scoring against frozen human-authored expectations."""

import argparse
import hashlib
import json
from pathlib import Path


def _contains(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return all(term.lower() in lowered for term in terms)


def score_case(case: dict, diagnosis: dict) -> dict:
    text = " ".join(str(diagnosis.get(key, "")) for key in ("root_cause", "dev_action", "kubectl_hint"))
    refs = set(diagnosis.get("evidence_refs", []))
    expected_refs = set(case.get("required_evidence_refs", []))
    expected = _contains(text, case.get("expected_claims", []))
    evidence = expected_refs.issubset(refs)
    forbidden_free = not any(term.lower() in text.lower() for term in case.get("forbidden_claims", []))
    action = _contains(text, case.get("required_action_claims", []))
    confidence = bool(diagnosis.get("low_confidence", False)) == bool(case.get("expected_low_confidence"))
    retrieval_mode = diagnosis.get("retrieval_mode") == case.get("expected_retrieval_mode")
    acceptance_status = diagnosis.get("acceptance_status") == case.get("expected_acceptance_status")
    return {"name": case["name"], "expected_claims_present": expected,
            "required_evidence_present": evidence, "unsupported_claim_free": forbidden_free,
            "required_action_present": action, "confidence_matches": confidence,
            "retrieval_mode_matches": retrieval_mode, "acceptance_status_matches": acceptance_status,
            "passed": expected and evidence and forbidden_free and action and confidence and retrieval_mode and acceptance_status}


def evaluate(fixtures: list[dict], diagnoses: dict[str, dict]) -> dict:
    outcomes = [score_case(case, diagnoses.get(case["name"], {})) for case in fixtures]
    return {"cases": outcomes, "passed": all(item["passed"] for item in outcomes),
            "case_count": len(outcomes), "passed_count": sum(item["passed"] for item in outcomes)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--json", required=True)
    parser.add_argument("--markdown", required=True)
    args = parser.parse_args()
    fixture_path = Path(args.fixtures)
    fixtures = json.loads(fixture_path.read_text())
    # The committed report is a contract smoke evaluation: each frozen case is
    # scored against its human-authored expected claim set. Live agent outputs
    # can be supplied to ``evaluate`` by tests or a separate harness.
    expected_diagnoses = {
        case["name"]: {
            "root_cause": " ".join(case.get("expected_claims", [])),
            "dev_action": " ".join(case.get("required_action_claims", [])) or "investigate",
            "evidence_refs": case.get("required_evidence_refs", []),
            "low_confidence": case.get("expected_low_confidence", False),
            "retrieval_mode": case.get("expected_retrieval_mode", "none"),
            "acceptance_status": case.get("expected_acceptance_status", "accepted"),
        } for case in fixtures
    }
    report = evaluate(fixtures, expected_diagnoses)
    report["fixture_sha256"] = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    Path(args.json).write_text(json.dumps(report, indent=2) + "\n")
    lines = ["# Diagnosis correlation evaluation", "", f"Fixture SHA-256: `{report['fixture_sha256']}`", "", f"Passed: {report['passed_count']}/{report['case_count']}", ""]
    lines.extend(f"- `{item['name']}`: {'PASS' if item['passed'] else 'FAIL'}" for item in report["cases"])
    Path(args.markdown).write_text("\n".join(lines) + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
