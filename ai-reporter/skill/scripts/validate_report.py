#!/usr/bin/env python3
"""
Validates report.json against the expected schema.
Prints PASS or FAIL: <reason>.
Exits 0 on PASS, 1 on FAIL.

Supports both report mode (base schema) and verify mode (extended schema).
Verify-mode fields are optional — only validated if present (backward-compatible).
"""
import json
import sys

VALID_STATUSES = {"OK", "WARN", "CRIT"}
VALID_RISK     = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
VALID_CONF     = {"LOW", "MEDIUM", "HIGH"}


def validate(path: str) -> tuple[bool, str]:
    try:
        with open(path) as f:
            report = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return False, f"cannot parse report file: {exc}"

    if report.get("overall_status") not in VALID_STATUSES:
        return False, f"overall_status must be OK|WARN|CRIT, got: {report.get('overall_status')!r}"

    summary = report.get("summary", "")
    if not summary or not isinstance(summary, str):
        return False, "summary must be a non-empty string"

    services = report.get("services", [])
    if not isinstance(services, list) or len(services) == 0:
        return False, "services must be a non-empty array"

    for svc in services:
        if not svc.get("name"):
            return False, "each service entry must have a non-empty name"
        if svc.get("status") not in VALID_STATUSES:
            return False, f"service {svc.get('name')!r} status must be OK|WARN|CRIT"
        if not svc.get("finding"):
            return False, f"service {svc.get('name')!r} must have a non-empty finding"

    recs = report.get("recommendations", [])
    if not isinstance(recs, list) or len(recs) == 0:
        return False, "recommendations must be a non-empty array"
    if len(recs) > 5:
        return False, f"recommendations has {len(recs)} entries (max 5)"
    if any(not r for r in recs):
        return False, "recommendations must not contain empty strings"

    # Verify-mode fields: only validate if present (backward-compatible with report mode)
    if "regression_detected" in report:
        if not isinstance(report.get("regression_detected"), bool):
            return False, "regression_detected must be a boolean"

    if "deployment_risk" in report:
        if report.get("deployment_risk") not in VALID_RISK:
            return False, f"deployment_risk must be LOW|MEDIUM|HIGH|CRITICAL, got: {report.get('deployment_risk')!r}"

    if "confidence" in report:
        if report.get("confidence") not in VALID_CONF:
            return False, f"confidence must be LOW|MEDIUM|HIGH, got: {report.get('confidence')!r}"

    if "kargo_annotation" in report:
        if not isinstance(report.get("kargo_annotation"), str):
            return False, "kargo_annotation must be a string"

    return True, "ok"


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "report.json"
    ok, reason = validate(path)
    if ok:
        print("PASS")
        return 0
    print(f"FAIL: {reason}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
