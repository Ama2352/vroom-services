import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from github_client import GitHubResult
from provenance import combine_provenance, collect_service_source_evidence, resolve_image_commit


class FakeServicesClient:
    def __init__(self, result):
        self.result = result
        self.requested_shas = []

    def get_commit(self, sha):
        self.requested_shas.append(sha)
        return self.result


def test_ci_image_tag_resolves_to_exact_source_commit():
    client = FakeServicesClient(GitHubResult("available", {
        "sha": "dc2136860123456789abcdef",
        "html_url": "https://github.example/commit/dc213686",
        "commit": {
            "message": "fix: emit new event contract\n\nbody",
            "author": {"name": "Dev", "date": "2026-08-09T10:00:00Z"},
        },
        "files": [{
            "filename": "services/ride/internal/events/producer.go",
            "patch": "+ eventType := \"Payment.Completed.v3\"",
        }],
    }))

    result = resolve_image_commit(
        "ghcr.io/example/ride-service:v1.0.0-build.z.114.dc213686",
        client,
    )

    assert result["status"] == "found"
    assert result["commit"]["sha"] == "dc2136860123456789abcdef"
    assert result["commit"]["message"] == "fix: emit new event contract"
    assert client.requested_shas == ["dc213686"]


def test_mutable_image_tag_is_unavailable_without_latest_commit_fallback():
    client = FakeServicesClient(GitHubResult("available", {"sha": "unexpected"}))

    result = resolve_image_commit("ghcr.io/example/ride-service:latest", client)

    assert result == {"status": "unavailable", "reason": "source_revision_not_encoded"}
    assert client.requested_shas == []


def test_service_source_evidence_is_bounded_and_preserves_changed_files():
    files = [
        {"filename": f"services/ride/file-{index}.go", "patch": "+ change"}
        for index in range(25)
    ]
    client = FakeServicesClient(GitHubResult("available", {
        "sha": "a1b2c3d4ef",
        "html_url": "https://github.example/commit/a1b2c3d4",
        "commit": {"message": "feat: bounded evidence", "author": {"name": "Dev", "date": "2026-08-09T10:00:00Z"}},
        "files": files,
    }))

    result = collect_service_source_evidence(
        "ghcr.io/example/ride-service:v1.0.0-build.z.2.a1b2c3d4",
        client,
    )

    assert result["status"] == "found"
    assert len(result["changed_files"]) == 20
    assert result["changed_files"][0]["path"] == "services/ride/file-0.go"


def test_combined_provenance_keeps_gitops_and_service_evidence_separate():
    combined = combine_provenance(
        {
            "classification": "gitops-commit",
            "commit": {"sha": "gitops123"},
            "env_diff": [{"key": "FEATURE_MODE", "old_value": "v1", "new_value": "v2"}],
        },
        {
            "status": "found",
            "commit": {"sha": "service123"},
            "changed_files": [{"path": "services/ride/producer.go", "patch": "+ Payment.Completed.v3"}],
        },
    )

    assert combined["gitops"]["commit"]["sha"] == "gitops123"
    assert combined["service_source"]["commit"]["sha"] == "service123"
    assert combined["service_source"]["changed_files"][0]["path"] == "services/ride/producer.go"
