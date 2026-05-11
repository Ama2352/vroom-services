#!/usr/bin/env python3
"""
Vroom AI Reporter — orchestrates the health analysis workflow.

Modes:
  --mode report (default): collect → analyze → post Slack
  --mode verify           : collect → analyze → post Slack → exit(1) if CRIT
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
SKILL_DIR  = os.path.join(os.path.dirname(__file__), "skill")


def run_script(script: str, *args: str) -> tuple[int, str]:
    cmd    = [sys.executable, os.path.join(SKILL_DIR, "scripts", script), *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode, result.stdout


def call_gemini(metrics: dict, retry_feedback: str = "") -> dict:
    schema    = json.load(open(f"{SKILL_DIR}/examples/report-schema.json"))
    few_shot  = json.load(open(f"{SKILL_DIR}/examples/few-shot-example.json"))
    thresholds = open(f"{SKILL_DIR}/references/thresholds.md").read()

    system = (
        "You are an SRE analyzing the Vroom ride-hailing platform.\n"
        f"Thresholds:\n{thresholds}\n\n"
        "Example of correct analysis:\n"
        f"INPUT: {json.dumps(few_shot['example_input'])}\n"
        f"OUTPUT: {json.dumps(few_shot['example_output'])}\n\n"
        "Analyze the provided metrics. Follow the exact same structure."
    )
    if retry_feedback:
        system += f"\n\nPrevious attempt failed validation: {retry_feedback}. Fix the issue."

    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel(
        model_name="gemini-flash-latest",
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=schema,
            max_output_tokens=2048,
            temperature=0.1,
        ),
        system_instruction=system,
    )
    response = model.generate_content(f"Analyze these metrics:\n{json.dumps(metrics)}")
    return json.loads(response.text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Vroom AI health reporter")
    parser.add_argument(
        "--mode",
        choices=["report", "verify"],
        default="report",
        help="report=post Slack; verify=post Slack + exit(1) on CRIT (for Kargo gates)",
    )
    args = parser.parse_args()

    # Verify mode: wait for deployment to stabilize before collecting
    if args.mode == "verify":
        wait = int(os.environ.get("STABILIZATION_WAIT_SECONDS", "0"))
        if wait:
            print(f"[verify] Waiting {wait}s for deployment to stabilize…")
            time.sleep(wait)

    # Step 1: Collect metrics
    exit_code, _ = run_script("collect_metrics.py")
    # We now always proceed to Gemini analysis to ensure metrics are reported to Slack,
    # regardless of whether an anomaly was strictly detected by thresholds.

    metrics = json.load(open("metrics.json"))

    # Step 2: Analyze with Gemini Flash
    print("Calling Gemini Flash for analysis…")
    report = call_gemini(metrics)
    json.dump(report, open("report.json", "w"), indent=2)

    # Step 3: Validate output
    rc, output = run_script("validate_report.py", "report.json")
    if rc != 0:
        print(f"Validation failed: {output.strip()} — retrying…")
        report = call_gemini(metrics, retry_feedback=output.strip())
        json.dump(report, open("report.json", "w"), indent=2)
        rc, _ = run_script("validate_report.py", "report.json")
        if rc != 0:
            run_script("post_slack.py", "--unavailable")
            return 1 if args.mode == "verify" else 0

    # Step 4: Post to Slack
    run_script("post_slack.py", "report.json")

    # Verify mode: exit 1 on CRIT to signal Kargo gate failure
    if args.mode == "verify":
        status = report.get("overall_status", "CRIT")
        if status == "CRIT":
            print(f"[verify] CRIT detected — exiting 1 to fail Kargo analysis")
            return 1
        print(f"[verify] Status={status} — exiting 0 (promotion approved)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
