"""Evidence-preserving reports for the offline reranker tournament."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


_TOP_LEVEL_ORDER = (
    "schema_version", "generated_at", "decision", "recommendation", "environment",
    "dataset", "shared_candidates", "systems", "llm_repetitions",
    "informative_failures", "failure_reasons", "reproduction",
)
_SYSTEM_ORDER = ("baseline", "bm25", "minilm", "mixedbread_xsmall", "llm")
_SENSITIVE_KEYS = {"api_key", "apikey", "authorization", "authorization_header"}


def _ordered_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the complete serializable result while making its main fields readable."""
    return {
        key: result[key] for key in _TOP_LEVEL_ORDER if key in result
    } | {
        key: value for key, value in result.items() if key not in _TOP_LEVEL_ORDER
    }


def _assert_no_secrets(value: Any, path: str = "result") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                raise ValueError(f"refusing to write sensitive field at {path}.{key}")
            _assert_no_secrets(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_no_secrets(item, f"{path}[{index}]")


def _metric(system: Mapping[str, Any]) -> Mapping[str, Any]:
    return system.get("held_out") or {}


def _number(value: Any, *, digits: int = 1) -> str:
    return "n/a" if value is None else f"{float(value):.{digits}f}"


def _percent(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.1f}%"


def _system_row(name: str, systems: Mapping[str, Any]) -> str:
    system = systems.get(name) or {"status": "unavailable"}
    metrics = _metric(system)
    operational = system.get("operational") or {}
    no_match = metrics.get("no_match_cases")
    false_positives = metrics.get("false_positives")
    fp = "n/a" if no_match is None or false_positives is None else f"{false_positives}/{no_match} ({_percent(metrics.get('false_positive_rate'))})"
    return (
        f"| {name} | {_percent(metrics.get('top1_accuracy'))} | "
        f"{_percent(metrics.get('recall_at_3'))} | {_number(metrics.get('mean_reciprocal_rank'), digits=3)} | "
        f"{fp} | {metrics.get('forbidden_acceptances', 'n/a')} | "
        f"{metrics.get('exact_failures', 'n/a')} | {str(system.get('stable', False)).lower()} | "
        f"{_number(operational.get('p95_ms'))} ms | {_number(operational.get('peak_rss_mb'))} MB |"
    )


def _dns_trace(result: Mapping[str, Any]) -> str:
    candidates = ((result.get("shared_candidates") or {}).get("dns_no_match") or {}).get("candidates") or []
    return (
        f"- `dns_no_match` — DNS hard negative: expected abstention; shared retrieval "
        f"presented {len(candidates)} candidate(s), so the semantic stage must not accept one."
    )


def _additional_trace(result: Mapping[str, Any]) -> str | None:
    traces = result.get("informative_failures") or []
    for trace in traces:
        if trace.get("case_id") != "dns_no_match":
            return f"- `{trace.get('case_id', trace.get('system', 'trace'))}` — {trace.get('reason', trace.get('message', 'recorded failure trace'))}."
    systems = result.get("systems") or {}
    priority = ("forbidden acceptance", "false positive", "missed positive", "unstable")
    choices = []
    for name, system in systems.items():
        metrics = _metric(system)
        reasons = " ".join(str(reason) for reason in system.get("failure_reasons", []))
        if metrics.get("forbidden_acceptances", 0):
            choices.append((0, name, "forbidden acceptance"))
        elif metrics.get("false_positives", 0):
            choices.append((1, name, "false positive"))
        elif metrics.get("top1_correct", metrics.get("positive_cases", 0)) < metrics.get("positive_cases", 0):
            choices.append((2, name, "missed positive"))
        elif not system.get("stable", True):
            choices.append((3, name, "unstable LLM" if system.get("kind") == "llm" else "unstable result"))
        elif reasons:
            choices.append((len(priority), name, reasons))
    if choices:
        _, name, reason = min(choices)
        return f"- `{name}` — {reason}."
    return None


def _operational_line(systems: Mapping[str, Any], llm_repetitions: Mapping[str, Any]) -> str:
    local = []
    for name in ("minilm", "mixedbread_xsmall"):
        operational = (systems.get(name) or {}).get("operational") or {}
        local.append(
            f"{name} {_number(operational.get('artifact_mb'))}/{_number(operational.get('estimated_container_delta_mb'))} MB"
        )
    llm = systems.get("llm") or {}
    operational = llm.get("operational") or {}
    malformed = 0
    for case in llm_repetitions.values():
        malformed += sum(trace.get("parse_outcome") not in {"parsed", "bypassed"} for trace in case.get("runs", []))
    provider = llm.get("provider_failures", 0)
    return (
        "Operational telemetry: local artifact/estimated-container sizes: "
        f"{'; '.join(local)}; LLM {operational.get('request_count', 0)} request(s), "
        f"{malformed} malformed and {provider} provider failure(s), "
        f"{operational.get('input_tokens', 0)} input + {operational.get('output_tokens', 0)} output tokens, "
        f"paid-equivalent ${_number(operational.get('theoretical_spend_usd'), digits=6)}."
    )


def render_concise_markdown(result: Mapping[str, Any]) -> str:
    """Render the bounded human summary from the exact JSON-shaped result."""
    dataset = result.get("dataset") or {}
    systems = result.get("systems") or {}
    recommendation = result.get("recommendation") or {}
    recommendation_name = recommendation.get("name", "none")
    command = (result.get("reproduction") or {}).get(
        "command", "python -m evaluation.tournament --report-dir evaluation/reports"
    )
    lines = [
        "# Reranker Tournament",
        "",
        f"Decision: **{result.get('decision', 'INCOMPLETE')}**; recommendation: **{recommendation_name}**.",
        "",
        "## Why",
        "",
        (
            f"Offline evidence compares frozen candidates across {dataset.get('case_count', 'n/a')} cases: "
            f"{dataset.get('calibration_count', 'n/a')} calibration and {dataset.get('held_out_count', 'n/a')} held-out, "
            f"with {dataset.get('positive_count', 'n/a')} positive and {dataset.get('no_match_count', 'n/a')} no-match cases."
        ),
        "",
        "## Systems",
        "",
        "| System | Top-1 | Recall@3 | MRR | False positive | Forbidden | Exact | Stable | p95 | RSS |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        *[_system_row(name, systems) for name in _SYSTEM_ORDER],
        "",
        "## Results",
        "",
        _operational_line(systems, result.get("llm_repetitions") or {}),
        "",
        "## Informative failures",
        "",
        _dns_trace(result),
    ]
    additional = _additional_trace(result)
    if additional:
        lines.append(additional)
    lines.extend([
        "",
        "## Decision",
        "",
        f"{result.get('decision', 'INCOMPLETE')} selects {recommendation_name}; only LOCAL_PASS permits a free local rollout candidate.",
        "",
        "## Limitations",
        "",
        "This is an offline, fixed-fixture experiment; unavailable systems and provider failures are evidence, not production changes.",
        "",
        "## Interview explanation",
        "",
        "Candidate generation freezes the same BM25 top-eight candidates for every challenger. Semantic decision reranks only those candidates and never expands the retrieval set. Abstention keeps unsupported incidents from becoming advisory diagnoses. The proof gate requires held-out quality, false-positive, stability, latency, and memory evidence. The production boundary remains unchanged because this tournament is an offline evaluator.",
        "",
        "## Reproduce",
        "",
        f"`{command}` — [reranker-tournament.json](reranker-tournament.json)",
        "",
    ])
    return "\n".join(lines)


def write_reports(result: Mapping[str, Any], report_dir: Path) -> tuple[Path, Path]:
    """Write the complete machine record and a concise, non-secret presentation."""
    _assert_no_secrets(result)
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "reranker-tournament.json"
    markdown_path = report_dir / "reranker-tournament.md"
    json_path.write_text(
        json.dumps(_ordered_result(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_concise_markdown(result), encoding="utf-8")
    return json_path, markdown_path
