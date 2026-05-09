---
name: vroom-monitor-report
description: Analyzes Vroom ride-hailing platform RED metrics and Loki error logs to
             generate operational health reports. Use this skill when asked to analyze
             Prometheus metrics, check service health, or generate a platform health
             summary report.
---
# Vroom Monitor: Health Analysis

## 1. Quick Start
Run `python skill/scripts/collect_metrics.py` to gather current metrics.
See `skill/references/thresholds.md` for anomaly definitions.

## 2. Core Workflow
1. **Collect:** `python skill/scripts/collect_metrics.py`
   - Outputs `metrics.json`
   - Exits 0 if all services are within thresholds, exits 1 if any anomaly detected
2. **Pre-filter:** If exit code is 0 — post "✅ All Vroom services nominal" to Slack and stop.
   Do NOT call Gemini when exit code is 0.
3. **Analyze:** Call Gemini API using:
   - `skill/examples/few-shot-example.json` as system instruction context (worked example)
   - `metrics.json` as the user message content
   - `skill/examples/report-schema.json` as the `response_schema` parameter
4. **Validate:** `python skill/scripts/validate_report.py report.json`
   - If FAIL: retry once, providing the validation error as feedback in the next prompt
   - If second FAIL: post "⚠️ AI analysis unavailable — manual review required" and abort
5. **Post:** `python skill/scripts/post_slack.py report.json`

## 3. Constraints
- DO NOT call Gemini if collect_metrics exits with code 0
- Temperature MUST be 0.1 — factual analysis, not creative writing
- response_mime_type MUST be "application/json"
- DO NOT alter the structure in examples/report-schema.json
- Secrets come from environment variables only — never hardcode them

## 4. References
- Threshold values: `references/thresholds.md` (load on demand when building prompt)
- Output schema: `examples/report-schema.json` (load on demand for response_schema parameter)
- Worked example: `examples/few-shot-example.json` (load on demand for system_instruction)
