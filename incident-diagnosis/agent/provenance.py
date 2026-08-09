"""Bounded provenance helpers for deployed application source revisions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from github_client import GitHubClient


_SOURCE_REVISION = re.compile(r"\.([0-9a-f]{7,40})$", re.IGNORECASE)
_MAX_CHANGED_FILES = 20
_MAX_PATCH_CHARS = 1200
_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9][A-Za-z0-9._:-]{2,})(?![A-Za-z0-9])")


@dataclass(frozen=True)
class CausalityResult:
    status: Literal["causal_candidate", "recent_context", "conflicting", "unavailable"]
    reason_codes: tuple[str, ...]
    matched_identifiers: tuple[str, ...]


def _image_revision(image: str) -> str:
    tag = image.rsplit(":", 1)[-1] if ":" in image else ""
    match = _SOURCE_REVISION.search(tag)
    return match.group(1) if match else ""


def _commit_summary(detail: dict) -> dict:
    commit = detail.get("commit") or {}
    author = commit.get("author") or {}
    return {
        "sha": str(detail.get("sha", "")),
        "url": str(detail.get("html_url", "")),
        "message": str(commit.get("message", "")).split("\n", 1)[0],
        "author": str(author.get("name", "")),
        "date": str(author.get("date", "")),
    }


def _changed_files(detail: dict) -> list[dict]:
    files = detail.get("files") or []
    return [
        {
            "path": str(item.get("filename", "")),
            "status": str(item.get("status", "")),
            "patch": str(item.get("patch", ""))[:_MAX_PATCH_CHARS],
        }
        for item in files[:_MAX_CHANGED_FILES]
        if item.get("filename")
    ]


def resolve_image_commit(image: str, services_client: GitHubClient) -> dict:
    """Resolve an immutable CI image tag to its exact service-source commit."""
    revision = _image_revision(image)
    if not revision:
        return {"status": "unavailable", "reason": "source_revision_not_encoded"}

    result = services_client.get_commit(revision)
    if result.status != "available" or not isinstance(result.value, dict):
        return {"status": "unavailable", "reason": result.reason or "source_commit_unavailable"}

    return {
        "status": "found",
        "image": image,
        "source_revision": revision,
        "commit": _commit_summary(result.value),
        "changed_files": _changed_files(result.value),
    }


def collect_service_source_evidence(image: str, services_client: GitHubClient) -> dict:
    """Collect bounded source evidence for the deployed image only."""
    return resolve_image_commit(image, services_client)


def collect_deployed_identity(service: str, namespace: str, deployment_reader) -> dict:
    """Return the image currently running for a workload through an injected reader."""
    try:
        deployment = deployment_reader(service, namespace) or {}
        containers = deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        image = next((str(container.get("image", "")) for container in containers if container.get("image")), "")
    except Exception:
        image = ""
    if not image:
        return {"status": "unavailable", "reason": "workload_image_unavailable"}
    return {"status": "found", "service": service, "namespace": namespace, "image": image}


def combine_provenance(gitops: dict | None, service_source: dict | None) -> dict:
    """Keep GitOps deployment context distinct from application source evidence."""
    return {
        "gitops": dict(gitops or {"status": "unavailable", "reason": "gitops_unavailable"}),
        "service_source": dict(service_source or {"status": "unavailable", "reason": "service_source_unavailable"}),
    }


def _identifiers(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in _IDENTIFIER.finditer(text or ""):
        value = match.group(1).rstrip(".,;:)")
        if not any(marker in value for marker in (".", "-", ":")):
            continue
        found.setdefault(value.lower(), value)
        host = value.split(":", 1)[0]
        if "-" in host or "." in host:
            found.setdefault(host.lower(), host)
    return found


def _change_text(provenance: dict) -> str:
    parts: list[str] = []
    gitops = provenance.get("gitops") or {}
    for change in gitops.get("env_diff") or []:
        parts.append(str(change.get("new_value", "")))
    parts.extend((str(gitops.get(key, "")) for key in ("diff", "diff_snippet")))
    source = provenance.get("service_source") or {}
    for changed_file in source.get("changed_files") or []:
        parts.extend((str(changed_file.get("path", "")), str(changed_file.get("patch", ""))))
    return "\n".join(parts)


def _changed_at(provenance: dict) -> str:
    gitops = provenance.get("gitops") or {}
    return str(gitops.get("changed_at") or (gitops.get("commit") or {}).get("date") or "")


def classify_causality(
    provenance: dict,
    candidate_service: str,
    log_evidence: dict,
    trace_handoff: dict,
    alert_started_at: str,
    failure_predates: bool,
) -> CausalityResult:
    """Classify a deployment candidate from generic identity, ordering, and linkage signals."""
    gitops = provenance.get("gitops") or {}
    source = provenance.get("service_source") or {}
    has_deployed_identity = gitops.get("status") == "found" or source.get("status") == "found"
    if not has_deployed_identity:
        return CausalityResult("unavailable", ("deployed_identity_unavailable",), ())
    if failure_predates:
        return CausalityResult("conflicting", ("failure_predates_change",), ())
    if trace_handoff.get("status") == "conflict":
        return CausalityResult("conflicting", ("trace_conflict",), ())

    changed_at = _changed_at(provenance)
    if changed_at and alert_started_at and changed_at > alert_started_at:
        return CausalityResult("conflicting", ("change_after_alert",), ())

    involved = set(trace_handoff.get("involved_services") or trace_handoff.get("service_path") or [])
    log_service = str(log_evidence.get("service", ""))
    related = candidate_service in involved or candidate_service == log_service
    if not related:
        return CausalityResult("recent_context", ("service_not_in_failure_path",), ())

    observed = _identifiers(
        "\n".join((str(log_evidence.get("message", "")), str(trace_handoff.get("error_message", ""))))
    )
    changed = _identifiers(_change_text(provenance))
    shared = tuple(observed[key] for key in observed.keys() & changed.keys())
    if not shared:
        return CausalityResult("recent_context", ("deployed_identity", "service_related", "no_failure_linkage"), ())

    reasons = ["deployed_identity", "service_related", "exact_failure_identifier"]
    if changed_at and alert_started_at and changed_at < alert_started_at:
        reasons.append("change_precedes_alert")
    return CausalityResult("causal_candidate", tuple(reasons), shared)
