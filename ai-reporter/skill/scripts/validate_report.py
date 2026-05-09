#!/usr/bin/env python3
"""
Validates report.json against the expected schema.
Prints PASS or FAIL: <reason>.
Exits 0 on PASS, 1 on FAIL.
"""
import json
import sys

VALID_STATUSES = {"OK", "WARN", "CRIT"}


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
    if len(recs) > 3:
        return False, f"recommendations has {len(recs)} entries (max 3)"
    if any(not r for r in recs):
        return False, "recommendations must not contain empty strings"

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
