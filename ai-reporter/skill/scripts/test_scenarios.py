#!/usr/bin/env python3
"""
Offline scenario test runner for the Vroom AI Reporter.

Tests all AI-detectable cases without a live cluster by using synthetic metric fixtures.
Uses MOCK_METRICS_FILE env var to inject fixtures into collect_metrics.py.

Requirements:
  - Run from the ai-reporter root directory (where reporter.py lives)
  - GEMINI_API_KEY set for full Gemini tests, OR MOCK_GEMINI=1 for flow/format-only tests

Usage:
  # Full test with real Gemini API:
  GEMINI_API_KEY=... python skill/scripts/test_scenarios.py

  # Flow + format test without API calls (MOCK_GEMINI=1 uses fixture reports):
  MOCK_GEMINI=1 python skill/scripts/test_scenarios.py

  # Run a single scenario by name:
  GEMINI_API_KEY=... python skill/scripts/test_scenarios.py --only 02_LOW_ERRORS
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR   = Path(__file__).parent
FIXTURES_DIR = SCRIPT_DIR / "test_fixtures"
REPORTER     = Path(__file__).parent.parent.parent / "reporter.py"


def run_reporter(mode: str, env_overrides: dict, tmpdir: Path) -> tuple[int, str]:
    env = {
        **os.environ,
        "SLACK_WEBHOOK_URL": "",            # suppress Slack posts during tests
        **env_overrides,
    }
    result = subprocess.run(
        [sys.executable, str(REPORTER), f"--mode={mode}"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmpdir),
    )
    return result.returncode, result.stdout + result.stderr


def load_report(tmpdir: Path) -> dict:
    report_path = tmpdir / "report.json"
    if not report_path.exists():
        return {}
    with open(report_path) as f:
        return json.load(f)


def load_gate_verdict(tmpdir: Path) -> dict:
    path = tmpdir / "gate-verdict.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def scenario(
    name: str,
    metrics_fixture: str,
    mode: str = "report",
    baseline_fixture: str = "",
    mock_report_fixture: str = "",
    extra_env: dict = None,
    assert_fn=None,
    description: str = "",
):
    return {
        "name": name,
        "description": description,
        "metrics_fixture": metrics_fixture,
        "baseline_fixture": baseline_fixture,
        "mock_report_fixture": mock_report_fixture,
        "mode": mode,
        "extra_env": extra_env or {},
        "assert_fn": assert_fn,
    }


def run_scenario(s: dict, only: str = "") -> tuple[bool, str]:
    if only and only.lower() not in s["name"].lower():
        return None, "skipped"

    metrics_path = FIXTURES_DIR / s["metrics_fixture"]
    if not metrics_path.exists():
        return False, f"fixture not found: {metrics_path}"

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)

        mock_gemini = os.environ.get("MOCK_GEMINI") == "1"
        env = {
            "MOCK_METRICS_FILE": str(metrics_path),
            **s["extra_env"],
        }
        if mock_gemini and s["mock_report_fixture"]:
            mock_report_path = FIXTURES_DIR / s["mock_report_fixture"]
            if mock_report_path.exists():
                env["MOCK_GEMINI"] = "1"
                env["MOCK_REPORT_FILE"] = str(mock_report_path)
        elif mock_gemini:
            env["MOCK_GEMINI"] = "1"

        if s["baseline_fixture"]:
            # Copy baseline fixture; collect_metrics.py --baseline will use MOCK_METRICS_FILE
            baseline_path = FIXTURES_DIR / s["baseline_fixture"]
            if baseline_path.exists():
                shutil.copy(baseline_path, tmpdir / "baseline_metrics.json")
            env["MOCK_BASELINE_FILE"] = str(baseline_path)

        exit_code, output = run_reporter(s["mode"], env, tmpdir)
        report = load_report(tmpdir)
        gate   = load_gate_verdict(tmpdir)

        try:
            s["assert_fn"](exit_code, output, report, gate)
            return True, ""
        except AssertionError as exc:
            return False, f"{exc}\n--- Output ---\n{output[:2000]}"


SCENARIOS = [
    scenario(
        name="01_HEALTHY",
        description="All OK, error_logs=0 everywhere — pre-filter should bypass Gemini",
        metrics_fixture="scenario_01_healthy.json",
        mode="report",
        assert_fn=lambda code, out, rep, gate: (
            _assert(code == 0, f"expected exit 0, got {code}") or
            _assert(
                "skipping Gemini" in out or "All services healthy" in out or "nominal" in out.lower(),
                f"expected pre-filter or healthy message in output, got: {out[:500]}"
            )
        ),
    ),
    scenario(
        name="02_LOW_ERRORS",
        description="error_logs=2 (the error10 bug) — findings must say '2 error logs', status OK",
        metrics_fixture="scenario_02_low_errors.json",
        mock_report_fixture="mock_reports/scenario_02_low_errors_report.json",
        mode="report",
        assert_fn=lambda code, out, rep, gate: (
            _assert(code == 0, f"expected exit 0, got {code}") or
            _assert(rep.get("overall_status") == "OK",
                    f"expected overall_status=OK, got {rep.get('overall_status')}") or
            _assert_findings_no_false_no_errors(rep, services=["ride-service", "notification-service"])
        ),
    ),
    scenario(
        name="03_WARN_ERROR_RATE",
        description="ride-service error_rate=2.5% (WARN) — overall_status=WARN",
        metrics_fixture="scenario_03_warn_error_rate.json",
        mock_report_fixture="mock_reports/scenario_03_warn_report.json",
        mode="report",
        assert_fn=lambda code, out, rep, gate: (
            _assert(code == 0, f"expected exit 0, got {code}") or
            _assert(rep.get("overall_status") == "WARN",
                    f"expected overall_status=WARN, got {rep.get('overall_status')}")
        ),
    ),
    scenario(
        name="04_CRIT_ERROR_RATE",
        description="ride-service error_rate=7.0% (CRIT) — status CRIT; verify mode exits 1",
        metrics_fixture="scenario_04_crit_error_rate.json",
        mock_report_fixture="mock_reports/scenario_04_crit_report.json",
        mode="verify",
        extra_env={"STABILIZATION_WAIT_SECONDS": "0"},
        assert_fn=lambda code, out, rep, gate: (
            _assert(rep.get("overall_status") == "CRIT",
                    f"expected overall_status=CRIT, got {rep.get('overall_status')}") or
            _assert(code == 1, f"expected exit 1 (gate fail), got {code}") or
            _assert(gate.get("verdict") == "FAIL",
                    f"expected gate verdict=FAIL, got {gate.get('verdict')}")
        ),
    ),
    scenario(
        name="05_CRIT_LATENCY",
        description="ride-service p99=6.0s (CRIT) — overall_status=CRIT",
        metrics_fixture="scenario_05_crit_latency.json",
        mock_report_fixture="mock_reports/scenario_04_crit_report.json",
        mode="report",
        assert_fn=lambda code, out, rep, gate: (
            _assert(rep.get("overall_status") == "CRIT",
                    f"expected overall_status=CRIT, got {rep.get('overall_status')}")
        ),
    ),
    scenario(
        name="06_WARN_LOGS",
        description="ride-service error_logs=15 (WARN >10) — overall_status=WARN",
        metrics_fixture="scenario_06_warn_logs.json",
        mock_report_fixture="mock_reports/scenario_03_warn_report.json",
        mode="report",
        assert_fn=lambda code, out, rep, gate: (
            _assert(rep.get("overall_status") == "WARN",
                    f"expected overall_status=WARN, got {rep.get('overall_status')}")
        ),
    ),
    scenario(
        name="07_CRIT_LOGS",
        description="ride-service error_logs=55 (CRIT >50) — overall_status=CRIT",
        metrics_fixture="scenario_07_crit_logs.json",
        mock_report_fixture="mock_reports/scenario_04_crit_report.json",
        mode="report",
        assert_fn=lambda code, out, rep, gate: (
            _assert(rep.get("overall_status") == "CRIT",
                    f"expected overall_status=CRIT, got {rep.get('overall_status')}")
        ),
    ),
    scenario(
        name="08_MIXED",
        description="ride-service CRIT, others OK — overall_status=CRIT (worst service wins)",
        metrics_fixture="scenario_08_mixed.json",
        mock_report_fixture="mock_reports/scenario_04_crit_report.json",
        mode="report",
        assert_fn=lambda code, out, rep, gate: (
            _assert(rep.get("overall_status") == "CRIT",
                    f"expected overall_status=CRIT, got {rep.get('overall_status')}") or
            _assert(
                any(s.get("status") == "CRIT" and "ride" in s.get("name", "")
                    for s in rep.get("services", [])),
                "expected ride-service to be CRIT"
            )
        ),
    ),
    scenario(
        name="09_REGRESSION",
        description="baseline 0 errors, current 2 → regression_detected=true, gate PASS",
        metrics_fixture="scenario_09_regression.json",
        baseline_fixture="scenario_09_regression_baseline.json",
        mock_report_fixture="mock_reports/scenario_09_regression_report.json",
        mode="verify",
        extra_env={"STABILIZATION_WAIT_SECONDS": "0"},
        assert_fn=lambda code, out, rep, gate: (
            _assert(code == 0, f"expected exit 0 (PASS despite regression), got {code}") or
            _assert(gate.get("regression_detected") is True,
                    f"expected regression_detected=true, got {gate.get('regression_detected')}") or
            _assert(gate.get("verdict") == "PASS",
                    f"expected gate verdict=PASS, got {gate.get('verdict')}")
        ),
    ),
    scenario(
        name="10_OOM",
        description="ride-service oomkill_count=1 → deployment_risk=CRITICAL, gate FAIL",
        metrics_fixture="scenario_10_oom.json",
        mock_report_fixture="mock_reports/scenario_10_oom_report.json",
        mode="verify",
        extra_env={"STABILIZATION_WAIT_SECONDS": "0"},
        assert_fn=lambda code, out, rep, gate: (
            _assert(code == 1, f"expected exit 1 (gate fail on OOM), got {code}") or
            _assert(gate.get("deployment_risk") == "CRITICAL",
                    f"expected deployment_risk=CRITICAL, got {gate.get('deployment_risk')}") or
            _assert(gate.get("verdict") == "FAIL",
                    f"expected gate verdict=FAIL, got {gate.get('verdict')}")
        ),
    ),
]


def _assert(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def _assert_findings_no_false_no_errors(report: dict, services: list[str]):
    """Assert that findings for the given services mention the error count and NOT 'No errors'."""
    for svc_name in services:
        svc = next((s for s in report.get("services", []) if s.get("name") == svc_name), None)
        if svc is None:
            raise AssertionError(f"service {svc_name} not found in report")
        finding = svc.get("finding", "")
        if "no errors" in finding.lower() or "no error logs" in finding.lower():
            raise AssertionError(
                f"{svc_name}: finding says 'No errors' but service has error_logs > 0. "
                f"Finding: {finding!r}"
            )
        # Check that the finding mentions the numeric count
        if "2 error" not in finding and "error log" not in finding.lower():
            raise AssertionError(
                f"{svc_name}: finding does not mention error_logs count. Finding: {finding!r}"
            )


def main():
    only = ""
    if "--only" in sys.argv:
        idx = sys.argv.index("--only")
        if idx + 1 < len(sys.argv):
            only = sys.argv[idx + 1]

    mock_gemini = os.environ.get("MOCK_GEMINI") == "1"
    gemini_key  = os.environ.get("GEMINI_API_KEY", "")

    if not mock_gemini and not gemini_key:
        print("ERROR: Set GEMINI_API_KEY for full tests, or MOCK_GEMINI=1 for flow-only tests.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Vroom AI Reporter — Scenario Test Suite")
    if mock_gemini:
        print(f"  Mode: MOCK_GEMINI=1 (flow/format tests, no API calls)")
    else:
        print(f"  Mode: Full Gemini tests")
    print(f"{'='*60}\n")

    passed = 0
    failed = 0
    skipped = 0

    for s in SCENARIOS:
        ok, reason = run_scenario(s, only=only)
        if ok is None:
            skipped += 1
            print(f"  ⏭  {s['name']}: SKIPPED")
            continue
        if ok:
            passed += 1
            print(f"  ✅ {s['name']}: PASS")
        else:
            failed += 1
            print(f"  ❌ {s['name']}: FAIL")
            for line in reason.split("\n"):
                print(f"     {line}")

    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"{'='*60}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
