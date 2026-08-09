import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from github_client import GitHubClient, repository_clients_from_env


def test_repository_clients_use_explicit_repository_tokens(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_GITOPS_TOKEN", "gitops-read-token")
    monkeypatch.setenv("GITHUB_SERVICES_TOKEN", "services-read-token")
    monkeypatch.setenv("GITHUB_GITOPS_REPO", "example/gitops")
    monkeypatch.setenv("GITHUB_SERVICES_REPO", "example/services")

    clients = repository_clients_from_env()

    assert clients.gitops.repository == "example/gitops"
    assert clients.services.repository == "example/services"
    assert clients.gitops.token == "gitops-read-token"
    assert clients.services.token == "services-read-token"


def test_authentication_failure_is_safe_and_secret_free():
    response = MagicMock(status_code=401, ok=False, headers={})
    session = MagicMock()
    session.get.return_value = response

    result = GitHubClient("example/repo", "private-token", session=session).get_commit("abc1234")

    assert result.status == "unavailable"
    assert result.reason == "authentication_failed"
    assert "private-token" not in repr(result)


def test_successful_commit_returns_response_payload():
    response = MagicMock(status_code=200, ok=True, headers={})
    response.json.return_value = {"sha": "abc1234"}
    session = MagicMock()
    session.get.return_value = response

    result = GitHubClient("example/repo", "read-token", session=session).get_commit("abc1234")

    assert result.status == "available"
    assert result.value == {"sha": "abc1234"}
    assert session.get.call_args.kwargs["timeout"] == 10
