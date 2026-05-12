#!/usr/bin/env python3
"""
Posts health reports and Kargo gate verdicts to Slack.

Usage:
  python post_slack.py report.json           -- standard health report
  python post_slack.py report.json --verify  -- Kargo gate verdict (promotion decision)
  python post_slack.py --healthy             -- all nominal (pre-filter bypass)
  python post_slack.py --unavailable         -- analysis unavailable warning

Env vars:
  SLACK_WEBHOOK_URL  If empty, prints to stdout instead of posting
  TARGET_NAMESPACE   Used in message headers (default: vroom-dev)
"""
import json
import os
import sys

import requests

SLACK_URL  = os.environ.get("SLACK_WEBHOOK_URL", "")
NAMESPACE  = os.environ.get("TARGET_NAMESPACE", "vroom-dev")
MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash-lite")

STATUS_EMOJI = {"OK": "✅", "WARN": "⚠️", "CRIT": "🔴"}


def post(payload: dict) -> None:
    if not SLACK_URL:
        print("WARN: SLACK_WEBHOOK_URL not set — printing to stdout")
        print(json.dumps(payload, indent=2))
        return
    resp = requests.post(SLACK_URL, json=payload, timeout=10)
    resp.raise_for_status()
    print(f"Slack: posted ({resp.status_code})")


def post_healthy() -> None:
    post({"text": f"✅ *Vroom Platform* (`{NAMESPACE}`) — All services nominal. No anomalies detected."})


def post_unavailable() -> None:
    post({"text": f"⚠️ *Vroom AI Reporter* (`{NAMESPACE}`) — AI analysis unavailable after retry. Manual review recommended."})


def post_rate_limited() -> None:
    post({
        "text": (
            f"⚠️ *Vroom AI Reporter* (`{NAMESPACE}`) — AI analysis skipped: Gemini free-tier daily quota reached.\n"
            f"Promotion is *not blocked*. Manual health review recommended.\n"
            f"Model: `{MODEL_NAME}` (free tier: 1,500 RPD / 30 RPM). "
            f"Upgrade to a paid Gemini tier if this recurs."
        )
    })


def _service_lines(report: dict) -> list[str]:
    """Build per-service finding lines including log samples for WARN/CRIT."""
    lines = []
    for svc in report.get("services", []):
        s_status = svc.get("status", "")
        s_emoji  = STATUS_EMOJI.get(s_status, "❓")
        line = f"{s_emoji} *{svc['name']}*: {svc['finding']}"
        lines.append(line)
        # Show log samples for any service with errors in report (non-verify)
        samples = svc.get("error_log_samples", [])
        if samples and s_status in ("WARN", "CRIT"):
            lines.append("  _Error log samples:_")
            lines.extend(f"  `{s}`" for s in samples)
    return lines


def post_report(path: str) -> None:
    with open(path) as f:
        report = json.load(f)

    status  = report.get("overall_status", "UNKNOWN")
    emoji   = STATUS_EMOJI.get(status, "❓")
    summary = report.get("summary", "")

    service_lines = _service_lines(report)
    recs = report.get("recommendations", [])
    rec_lines = "\n".join(f"• {r}" for r in recs)

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} Vroom Health Report — {NAMESPACE}"}},
        {"type": "section", "text": {"type": "mrkdwn",   "text": f"*Status:* {status}\n{summary}"}},
        {"type": "section", "text": {"type": "mrkdwn",   "text": "\n".join(service_lines)}},
        {"type": "section", "text": {"type": "mrkdwn",   "text": f"*Recommendations:*\n{rec_lines}"}},
        {"type": "divider"},
    ]
    post({"blocks": blocks, "text": f"[{status}] Vroom Health — {NAMESPACE}"})


def post_verify_report(path: str) -> None:
    """Post a Kargo gate verdict with promotion decision and actionable recommendations."""
    with open(path) as f:
        report = json.load(f)

    status     = report.get("overall_status", "UNKNOWN")
    regression = report.get("regression_detected", False)
    risk       = report.get("deployment_risk", "UNKNOWN")
    confidence = report.get("confidence", "UNKNOWN")
    annotation = report.get("kargo_annotation", "")
    summary    = report.get("summary", "")

    # Determine gate verdict and Slack header
    verdict = "FAIL" if (status == "CRIT" or (regression and risk == "CRITICAL")) else "PASS"
    if verdict == "FAIL":
        header_emoji  = "🔴"
        header_text   = f"PROMOTE TO {NAMESPACE}: DO NOT PROMOTE"
        fallback_text = f"[DO NOT PROMOTE] Kargo Gate — {NAMESPACE} | {status}"
    elif regression:
        header_emoji  = "⚠️"
        header_text   = f"PROMOTE TO {NAMESPACE}: YES — WITH CAUTION"
        fallback_text = f"[PASS WITH CAUTION] Kargo Gate — {NAMESPACE} | {status}"
    else:
        header_emoji  = "✅"
        header_text   = f"PROMOTE TO {NAMESPACE}: YES"
        fallback_text = f"[PASS] Kargo Gate — {NAMESPACE} | {status}"

    regression_flag = "🔀 Regression detected" if regression else "✅ No regression"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{header_emoji} {header_text}"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Status:* {STATUS_EMOJI.get(status, '❓')} {status}"},
                {"type": "mrkdwn", "text": f"*Deployment Risk:* {risk}"},
                {"type": "mrkdwn", "text": f"*Confidence:* {confidence}"},
                {"type": "mrkdwn", "text": f"*Regression:* {regression_flag}"},
            ]
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Summary:* {summary}"}
        },
    ]

    # Per-service lines with regression tags and log samples
    svc_lines = []
    for svc in report.get("services", []):
        s_status = svc.get("status", "")
        s_emoji  = STATUS_EMOJI.get(s_status, "❓")
        reg_data = svc.get("regression", {})

        line = f"{s_emoji} *{svc['name']}*: {svc['finding']}"
        svc_lines.append(line)

        # Inline regression tag
        if reg_data.get("detected"):
            metric = reg_data.get("metric", "")
            base   = reg_data.get("baseline_value", 0)
            curr   = reg_data.get("current_value", 0)
            svc_lines.append(f"  ⚠️ _regression: {metric} {base}→{curr}_")

        # Pod health note
        pod_note = svc.get("pod_health_finding", "")
        if pod_note and ("restart" in pod_note.lower() or "oom" in pod_note.lower()):
            svc_lines.append(f"  🔁 _{pod_note}_")

        # Log samples
        samples = svc.get("error_log_samples", [])
        if not samples:
            # Try top-level metrics log samples (passed through in some flows)
            pass
        if samples:
            svc_lines.extend(f"  `{s}`" for s in samples[:3])

    if svc_lines:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(svc_lines)}
        })

    # Recommendations (framed as Actions / Monitor / nothing by Gemini)
    recs = report.get("recommendations", [])
    if recs:
        if verdict == "FAIL":
            rec_header = "*Actions required before promotion:*"
            rec_lines  = "\n".join(f"{i+1}. {r}" for i, r in enumerate(recs))
        elif regression:
            rec_header = "*Monitor after promotion:*"
            rec_lines  = "\n".join(f"• {r}" for r in recs)
        else:
            rec_header = "*Recommendations:*"
            rec_lines  = "\n".join(f"• {r}" for r in recs)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{rec_header}\n{rec_lines}"}
        })

    # Kargo annotation in small context block
    if annotation:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"Kargo annotation: `{annotation}`"}]
        })

    blocks.append({"type": "divider"})
    post({"blocks": blocks, "text": fallback_text})


def main() -> int:
    args = sys.argv[1:]
    verify_mode = "--verify" in args

    # Separate known action flags from file paths
    action  = next((a for a in args if a in ("--healthy", "--unavailable", "--rate-limited")), None)
    path    = next((a for a in args if not a.startswith("--")), "")

    try:
        if action == "--healthy":
            post_healthy()
        elif action == "--unavailable":
            post_unavailable()
        elif action == "--rate-limited":
            post_rate_limited()
        elif verify_mode:
            post_verify_report(path or "report.json")
        else:
            post_report(path or "report.json")
        return 0
    except Exception as exc:
        print(f"ERROR: Slack post failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
