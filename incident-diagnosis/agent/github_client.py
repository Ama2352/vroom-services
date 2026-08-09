"""Bounded, repository-specific GitHub API access for provenance collection."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import requests


GITHUB_API_URL = "https://api.github.com"


@dataclass(frozen=True)
class GitHubResult:
    status: str
    value: Any = None
    reason: str = ""


class GitHubClient:
    """Read-only, bounded GitHub API client for one repository."""

    def __init__(self, repository: str, token: str, session=requests):
        self.repository = repository
        self.token = token
        self.session = session

    def get_commit(self, sha: str) -> GitHubResult:
        return self._get(f"/repos/{self.repository}/commits/{sha}")

    def get_commit_files(self, sha: str) -> GitHubResult:
        result = self.get_commit(sha)
        if result.status != "available":
            return result
        files = result.value.get("files", ()) if isinstance(result.value, dict) else ()
        return GitHubResult("available", tuple(files)[:20])

    def read_file(self, path: str, ref: str) -> GitHubResult:
        return self._get(
            f"/repos/{self.repository}/contents/{path}",
            params={"ref": ref},
            raw=True,
        )

    def list_path_commits(self, path: str, ref: str = "", per_page: int = 20) -> GitHubResult:
        params = {"path": path, "per_page": min(per_page, 20)}
        if ref:
            params["sha"] = ref
        return self._get(f"/repos/{self.repository}/commits", params=params)

    def _get(self, path: str, params: dict | None = None, raw: bool = False) -> GitHubResult:
        try:
            response = self.session.get(
                f"{GITHUB_API_URL}{path}",
                params=params,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
                },
                timeout=10,
            )
        except requests.Timeout:
            return GitHubResult("unavailable", reason="timeout")
        except requests.RequestException:
            return GitHubResult("unavailable", reason="request_failed")

        if response.status_code == 401:
            return GitHubResult("unavailable", reason="authentication_failed")
        if response.status_code == 403:
            reason = "rate_limited" if response.headers.get("X-RateLimit-Remaining") == "0" else "forbidden"
            return GitHubResult("unavailable", reason=reason)
        if response.status_code == 404:
            return GitHubResult("unavailable", reason="not_found")
        if not response.ok:
            return GitHubResult("unavailable", reason=f"http_{response.status_code}")

        if raw:
            return GitHubResult("available", response.text)
        try:
            return GitHubResult("available", response.json())
        except ValueError:
            return GitHubResult("unavailable", reason="invalid_response")


@dataclass(frozen=True)
class RepositoryClients:
    gitops: GitHubClient
    services: GitHubClient


def repository_clients_from_env() -> RepositoryClients:
    return RepositoryClients(
        gitops=GitHubClient(
            os.environ.get("GITHUB_GITOPS_REPO", "Ama2352/vroom-gitops"),
            os.environ.get("GITHUB_GITOPS_TOKEN", ""),
        ),
        services=GitHubClient(
            os.environ.get("GITHUB_SERVICES_REPO", "Ama2352/vroom-services"),
            os.environ.get("GITHUB_SERVICES_TOKEN", ""),
        ),
    )
