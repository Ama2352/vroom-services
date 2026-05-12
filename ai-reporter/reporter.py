#!/usr/bin/env python3
"""
Vroom AI Reporter — orchestrates the health analysis workflow.

Modes:
  --mode report (default): collect → smart pre-filter → analyze → post Slack
  --mode verify           : collect + baseline + deploy context → analyze → gate decision → post Slack

Token efficiency:
  - Report mode skips Gemini entirely when all metrics are healthy and error_logs=0
  - User prompt uses compact pipe-delimited format (~120 tokens vs ~400 for JSON)
  - System instruction stays static (cached component); max_output_tokens capped per mode

Rate limit handling:
  - Model: gemini-2.0-flash-lite — free tier: 1,500 RPD / 30 RPM (vs 20 RPD for gemini-2.5-flash)
  - RPM (per-minute) 429: auto-retry once after 65s
  - RPD (daily quota) 429: post --rate-limited warning, exit 0 (never block a Kargo gate for quota)
  - CronJob overlap: dev (:00/:30) + staging (:00) = max 2 concurrent at :00 — safe under 30 RPM

Mock/test support:
  MOCK_METRICS_FILE: if set, collect_metrics.py uses that file instead of querying Prometheus/Loki
  MOCK_GEMINI:       if "1", reporter loads MOCK_REPORT_FILE instead of calling Gemini API
  MOCK_REPORT_FILE:  path to a report.json fixture for MOCK_GEMINI mode
"""
import argparse
import json
import os
import subprocess
import sys
import time

import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore", FutureWarning)
    import google.generativeai as genai

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
# gemini-2.0-flash-lite: 1,500 RPD / 30 RPM on free tier
# gemini-2.5-flash was 20 RPD — caused error11 rate limit incident
MODEL_NAME  = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash-lite")
SKILL_DIR  = os.path.join(os.path.dirname(__file__), "skill")

MANDATORY_RULES = """\
MANDATORY REPORTING RULES:
1. ALWAYS state the exact numeric error_logs count in every service finding, even if 0.
2. NEVER write 'No errors' or 'no error logs' when error_logs > 0.
   Write: '2 error logs (below WARN>10)' or '89 error logs (CRIT threshold exceeded)'.
3. ALWAYS include error_logs, error_rate_pct, and p99_latency_s in every service finding.
4. Status OK means within thresholds — NOT absence of issues. Always report actual values.
"""


class RateLimitError(Exception):
    """Raised when the Gemini API returns a daily quota (RPD) 429 that cannot be retried."""
    pass


def _generate_with_retry(model, prompt: str) -> str:
    """Call Gemini with one automatic retry for transient RPM-level 429s.

    Distinguishes two cases:
    - RPM (per-minute) limit: waits 65s and retries once. Recoverable.
    - RPD (per-day) quota: raises RateLimitError immediately. Unrecoverable today.

    The caller catches RateLimitError and exits 0 so rate limits never fail a Kargo gate.
    """
    for attempt in range(2):
        try:
            return model.generate_content(prompt).text
        except Exception as exc:
            msg = str(exc)
            is_rate_limit = (
                "429" in msg
                or "RESOURCE_EXHAUSTED" in msg
                or "quota" in msg.lower()
            )
            if not is_rate_limit:
                raise

            # "per minute" in the error message indicates an RPM limit — transient, retryable
            is_rpm = "per minute" in msg.lower() or "per_minute" in msg.lower()
            if attempt == 0 and is_rpm:
                print("WARN: Gemini RPM limit hit — waiting 65s before retry…", file=sys.stderr)
                time.sleep(65)
                continue

            # Anything else (RPD / daily quota) is unrecoverable until tomorrow
            raise RateLimitError(
                f"Gemini daily quota exhausted (model={MODEL_NAME}). "
                f"Free tier: 1,500 RPD for gemini-2.0-flash-lite. "
                f"Original error: {msg}"
            )

    raise RateLimitError("rate limit persists after RPM retry")


def load_json(path: str, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def run_script(script: str, *args: str) -> tuple[int, str]:
    env = os.environ.copy()
    cmd = [sys.executable, os.path.join(SKILL_DIR, "scripts", script), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode, result.stdout


def build_compact_prompt(metrics: dict) -> str:
    """Convert metrics dict to compact pipe-delimited format for token efficiency."""
    lines = [
        f"ns={metrics.get('namespace', '?')} window={metrics.get('window', '?')}",
        "svc|rps|err%|p99s|errlogs",
    ]
    for svc, d in metrics.get("services", {}).items():
        lines.append(
            f"{svc}|{d.get('req_rate_rps', 0.0)}|{d.get('error_rate_pct', 0.0)}"
            f"|{d.get('p99_latency_s', 0.0)}|{d.get('error_logs', 0)}"
        )
    # Append log samples as plain text (cheaper than JSON)
    for svc, d in metrics.get("services", {}).items():
        samples = d.get("error_log_samples", [])
        if samples:
            lines.append(f"\n{svc} error samples ({len(samples)}):")
            lines.extend(f"  {s}" for s in samples)
    return "\n".join(lines)


_MOCK_REPORT_DEFAULT = {
    "overall_status": "OK",
    "summary": "Mock report for testing — Gemini was called (pre-filter did not bypass).",
    "services": [
        {"name": "user-service",         "status": "OK", "finding": "Mock. 0 error logs, 0.0% error rate, p99=0.31s."},
        {"name": "ride-service",         "status": "OK", "finding": "Mock. 2 error logs (below WARN>10), 0.4% error rate, p99=0.55s."},
        {"name": "dispatch-service",     "status": "OK", "finding": "Mock. 0 error logs, 0.0% error rate, p99=0.09s."},
        {"name": "notification-service", "status": "OK", "finding": "Mock. 2 error logs (below WARN>10), 0.0% error rate, p99=0.19s."},
    ],
    "recommendations": ["Mock recommendation — run with GEMINI_API_KEY for real analysis."],
}

_MOCK_VERIFY_DEFAULT = {
    **_MOCK_REPORT_DEFAULT,
    "regression_detected": False,
    "deployment_risk":     "LOW",
    "confidence":          "MEDIUM",
    "kargo_annotation":    "Mock verify — run with GEMINI_API_KEY for real gate verdict.",
}


def call_gemini(metrics: dict, retry_feedback: str = "") -> dict:
    if os.environ.get("MOCK_GEMINI") == "1":
        mock_path = os.environ.get("MOCK_REPORT_FILE", "")
        return load_json(mock_path, _MOCK_REPORT_DEFAULT) if mock_path else _MOCK_REPORT_DEFAULT

    schema     = load_json(f"{SKILL_DIR}/examples/report-schema.json")
    few_shot   = load_json(f"{SKILL_DIR}/examples/few-shot-example.json")
    thresholds = open(f"{SKILL_DIR}/references/thresholds.md").read()

    ex1_in  = few_shot["example_ok_with_minor_logs"]["input"]
    ex1_out = json.dumps(few_shot["example_ok_with_minor_logs"]["output"])
    ex2_in  = few_shot["example_crit"]["input"]
    ex2_out = json.dumps(few_shot["example_crit"]["output"])

    system = (
        "You are an SRE analyzing the Vroom ride-hailing platform.\n"
        f"Thresholds:\n{thresholds}\n\n"
        f"{MANDATORY_RULES}\n"
        "Input format: compact pipe-delimited table (svc|rps|err%|p99s|errlogs)\n\n"
        f"EXAMPLE 1 INPUT:\n{ex1_in}\n"
        f"EXAMPLE 1 OUTPUT: {ex1_out}\n\n"
        f"EXAMPLE 2 INPUT:\n{ex2_in}\n"
        f"EXAMPLE 2 OUTPUT: {ex2_out}\n\n"
        "Analyze the provided metrics. Follow the exact same structure."
    )
    if retry_feedback:
        system += f"\n\nPrevious attempt failed validation: {retry_feedback}. Fix the issue."

    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=schema,
            max_output_tokens=1024,
            temperature=0.1,
        ),
        system_instruction=system,
    )
    # RateLimitError propagates to main() — never caught here
    response_text = _generate_with_retry(model, f"Analyze:\n{build_compact_prompt(metrics)}")
    return json.loads(response_text)


def call_gemini_verify(metrics: dict, baseline: dict, deploy_ctx: dict, retry_feedback: str = "") -> dict:
    if os.environ.get("MOCK_GEMINI") == "1":
        mock_path = os.environ.get("MOCK_REPORT_FILE", "")
        return load_json(mock_path, _MOCK_VERIFY_DEFAULT) if mock_path else _MOCK_VERIFY_DEFAULT

    schema      = load_json(f"{SKILL_DIR}/examples/report-schema-verify.json")
    few_shot    = load_json(f"{SKILL_DIR}/examples/few-shot-verify-example.json")
    thresholds  = open(f"{SKILL_DIR}/references/thresholds.md").read()
    reg_thresh  = open(f"{SKILL_DIR}/references/regression-thresholds.md").read()

    ex_in  = few_shot.get("example_input", {})
    ex_out = json.dumps(few_shot.get("example_output", {}))

    system = (
        "You are an SRE performing a post-deployment health gate for the Vroom platform.\n"
        f"Thresholds:\n{thresholds}\n\n"
        f"Regression Thresholds:\n{reg_thresh}\n\n"
        f"{MANDATORY_RULES}"
        "Additional verify-mode rules:\n"
        "5. ALWAYS compute regression delta vs baseline when baseline data is provided.\n"
        "6. Set regression_detected=true if ANY service exceeds regression thresholds.\n"
        "7. deployment_risk must be one of: LOW, MEDIUM, HIGH, CRITICAL.\n"
        "8. confidence: HIGH (baseline+current available), MEDIUM (current only), LOW (partial).\n"
        "9. RECOMMENDATION FORMAT:\n"
        "   - If overall_status=CRIT: frame as numbered 'Actions required before promotion' with kubectl commands.\n"
        "   - If regression_detected=true and NOT CRIT: frame as 'Monitor after promotion: watch for...'\n"
        "   - If PASS clean: write 'Platform is healthy. Proceed with promotion.'\n\n"
        f"EXAMPLE INPUT: {json.dumps(ex_in)}\n"
        f"EXAMPLE OUTPUT: {ex_out}\n\n"
        "Analyze the post-deploy metrics and produce a gate verdict."
    )
    if retry_feedback:
        system += f"\n\nPrevious attempt failed validation: {retry_feedback}. Fix the issue."

    # Build compact user prompt with optional baseline + deploy context
    parts = []
    if deploy_ctx:
        images = deploy_ctx.get("deployed_images", {})
        if images:
            parts.append("Deployed images: " + ", ".join(f"{k}={v.split('/')[-1]}" for k, v in images.items()))
        rollout = deploy_ctx.get("rollout_times", {})
        if rollout:
            latest = max(rollout.values()) if rollout else ""
            parts.append(f"Rollout time: {latest}")
    if baseline and baseline.get("services"):
        parts.append(f"Baseline (pre-deploy {baseline.get('window','?')}):")
        parts.append("svc|rps|err%|p99s|errlogs")
        for svc, d in baseline["services"].items():
            parts.append(f"{svc}|{d.get('req_rate_rps',0)}|{d.get('error_rate_pct',0)}|{d.get('p99_latency_s',0)}|{d.get('error_logs',0)}")
    parts.append(f"\nCurrent metrics:")
    parts.append(build_compact_prompt(metrics))

    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=schema,
            max_output_tokens=2048,
            temperature=0.1,
        ),
        system_instruction=system,
    )
    # RateLimitError propagates to main() — never caught here
    response_text = _generate_with_retry(model, "\n".join(parts))
    return json.loads(response_text)


def _handle_rate_limit(exc: RateLimitError, mode: str) -> int:
    """Handle a daily quota exhaustion: post a warning and always exit 0.

    Rate limits are infrastructure issues, not health failures.
    They must never block a Kargo promotion gate.
    """
    print(f"WARN: {exc}", file=sys.stderr)
    run_script("post_slack.py", "--rate-limited")
    if mode == "verify":
        with open("gate-verdict.json", "w") as f:
            json.dump({
                "verdict":         "PASS_RATE_LIMITED",
                "overall_status":  "UNKNOWN",
                "reason":          str(exc),
                "kargo_annotation": f"AI gate bypassed: Gemini rate limit. Manual health review required.",
            }, f, indent=2)
        print("[verify] Rate limited — exiting 0 (gate bypassed; manual review recommended)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Vroom AI health reporter")
    parser.add_argument(
        "--mode",
        choices=["report", "verify"],
        default="report",
        help="report=post Slack (with pre-filter); verify=post Slack + gate verdict (always runs Gemini)",
    )
    args = parser.parse_args()

    if args.mode == "verify":
        wait = int(os.environ.get("STABILIZATION_WAIT_SECONDS", "0"))
        if wait:
            print(f"[verify] Waiting {wait}s for deployment to stabilize…")
            time.sleep(wait)

        # Collect deploy context (non-fatal)
        run_script("collect_deploy_context.py")
        deploy_ctx = load_json("deploy_context.json", {})

        # Collect baseline metrics (non-fatal)
        run_script("collect_metrics.py", "--baseline")
        baseline = load_json("baseline_metrics.json", {})

    # Always collect current metrics
    collect_exit_code, _ = run_script("collect_metrics.py")
    metrics = load_json("metrics.json", {})

    if not metrics.get("services"):
        print("ERROR: metrics.json is empty or missing — aborting", file=sys.stderr)
        run_script("post_slack.py", "--unavailable")
        return 1 if args.mode == "verify" else 0

    # Smart pre-filter (report mode only) — skip Gemini when truly healthy and data is complete
    if args.mode == "report":
        all_instrumented = all(
            svc.get("instrumented", False)
            for svc in metrics["services"].values()
        )
        has_any_errors = any(
            svc.get("error_logs", 0) > 0
            for svc in metrics["services"].values()
        )
        has_pod_issues = any(
            svc.get("pod_health", {}).get("restart_count", 0) > 0
            or svc.get("pod_health", {}).get("oomkill_count", 0) > 0
            for svc in metrics["services"].values()
        )

        if collect_exit_code == 0 and all_instrumented and not has_any_errors and not has_pod_issues:
            print("STATUS: All services healthy and instrumented — skipping Gemini analysis (token efficiency)")
            run_script("post_slack.py", "--healthy")
            return 0
        
        if not all_instrumented:
            print("STATUS: Some services are not instrumented (missing Prometheus data) — Gemini analysis required")
        elif has_any_errors or has_pod_issues:
            print("STATUS: Minor issues detected (below WARN thresholds) — using Gemini for smart summary")

    # Analyze with Gemini
    print(f"Calling {MODEL_NAME} for analysis…")
    try:
        if args.mode == "verify":
            report = call_gemini_verify(metrics, baseline, deploy_ctx)
        else:
            report = call_gemini(metrics)
    except RateLimitError as exc:
        return _handle_rate_limit(exc, args.mode)

    with open("report.json", "w") as f:
        json.dump(report, f, indent=2)

    # Validate output
    rc, output = run_script("validate_report.py", "report.json")
    if rc != 0:
        print(f"Validation failed: {output.strip()} — retrying…")
        try:
            if args.mode == "verify":
                report = call_gemini_verify(metrics, baseline, deploy_ctx, retry_feedback=output.strip())
            else:
                report = call_gemini(metrics, retry_feedback=output.strip())
        except RateLimitError as exc:
            return _handle_rate_limit(exc, args.mode)

        with open("report.json", "w") as f:
            json.dump(report, f, indent=2)
        rc, _ = run_script("validate_report.py", "report.json")
        if rc != 0:
            run_script("post_slack.py", "--unavailable")
            return 1 if args.mode == "verify" else 0

    # Post to Slack
    if args.mode == "verify":
        run_script("post_slack.py", "report.json", "--verify")
    else:
        run_script("post_slack.py", "report.json")

    # Gate decision (verify mode)
    if args.mode == "verify":
        status      = report.get("overall_status", "CRIT")
        regression  = report.get("regression_detected", False)
        risk        = report.get("deployment_risk", "UNKNOWN")
        verdict     = "FAIL" if (status == "CRIT" or (regression and risk == "CRITICAL")) else "PASS"

        gate_verdict = {
            "verdict":              verdict,
            "overall_status":       status,
            "regression_detected":  regression,
            "confidence":           report.get("confidence", "MEDIUM"),
            "deployment_risk":      risk,
            "kargo_annotation":     report.get("kargo_annotation", ""),
        }
        with open("gate-verdict.json", "w") as f:
            json.dump(gate_verdict, f, indent=2)

        print(f"[verify] Gate verdict: {verdict} (status={status}, regression={regression}, risk={risk})")
        if verdict == "FAIL":
            print("[verify] FAIL — exiting 1 to fail Kargo analysis")
            return 1
        print("[verify] PASS — exiting 0 (promotion approved)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
