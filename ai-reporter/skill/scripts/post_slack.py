#!/usr/bin/env python3
"""
Posts a health report to Slack.

Usage:
  python post_slack.py report.json           -- post AI analysis report
  python post_slack.py --healthy             -- post "all nominal" message
  python post_slack.py --unavailable         -- post "analysis unavailable" warning
"""
import json
import os
import sys

import requests

SLACK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
NAMESPACE = os.environ.get("TARGET_NAMESPACE", "vroom-dev")

STATUS_EMOJI = {"OK": "✅", "WARN": "⚠️", "CRIT": "🔴"}


def post(payload: dict) -> None:
    if not SLACK_URL:
        print("WARN: SLACK_WEBHOOK_URL not set — printing report to stdout")
        print(json.dumps(payload, indent=2))
        return
    resp = requests.post(SLACK_URL, json=payload, timeout=10)
    resp.raise_for_status()
    print(f"Slack: posted ({resp.status_code})")


def post_healthy() -> None:
    post({
        "text": f"✅ *Vroom Platform* (`{NAMESPACE}`) — All services nominal. No anomalies detected.",
    })


def post_unavailable() -> None:
    post({
        "text": f"⚠️ *Vroom AI Reporter* (`{NAMESPACE}`) — AI analysis unavailable after retry. Manual review recommended.",
    })


def post_report(path: str) -> None:
    with open(path) as f:
        report = json.load(f)

    status  = report.get("overall_status", "UNKNOWN")
    emoji   = STATUS_EMOJI.get(status, "❓")
    summary = report.get("summary", "")

    service_lines = []
    for svc in report.get("services", []):
        s_emoji = STATUS_EMOJI.get(svc.get("status", ""), "❓")
        service_lines.append(f"{s_emoji} *{svc['name']}*: {svc['finding']}")

    recs = report.get("recommendations", [])
    rec_lines = "\n".join(f"• {r}" for r in recs)

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} Vroom Health Report — {NAMESPACE}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Status:* {status}\n{summary}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(service_lines)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Recommendations:*\n{rec_lines}"}},
        {"type": "divider"},
    ]
    post({"blocks": blocks, "text": f"[{status}] Vroom Health — {NAMESPACE}"})


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if arg == "--healthy":
            post_healthy()
        elif arg == "--unavailable":
            post_unavailable()
        else:
            post_report(arg or "report.json")
        return 0
    except Exception as exc:
        print(f"ERROR: Slack post failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
