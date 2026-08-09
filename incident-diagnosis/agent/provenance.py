"""Bounded provenance helpers for deployed application source revisions."""

from __future__ import annotations

import re

from github_client import GitHubClient


_SOURCE_REVISION = re.compile(r"\.([0-9a-f]{7,40})$", re.IGNORECASE)
_MAX_CHANGED_FILES = 20
_MAX_PATCH_CHARS = 1200


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


def combine_provenance(gitops: dict | None, service_source: dict | None) -> dict:
    """Keep GitOps deployment context distinct from application source evidence."""
    return {
        "gitops": dict(gitops or {"status": "unavailable", "reason": "gitops_unavailable"}),
        "service_source": dict(service_source or {"status": "unavailable", "reason": "service_source_unavailable"}),
    }
