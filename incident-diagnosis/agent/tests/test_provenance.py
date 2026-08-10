import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from github_client import GitHubResult
from provenance import (
    classify_causality,
    combine_provenance,
    collect_deployed_identity,
    collect_service_source_evidence,
    resolve_image_commit,
    summarize_provenance,
    select_gitops_change,
)


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


def test_service_source_evidence_keeps_only_files_for_the_affected_service():
    client = FakeServicesClient(GitHubResult("available", {
        "sha": "a1b2c3d4ef",
        "commit": {"message": "monorepo build", "author": {}},
        "files": [
            {"filename": "incident-diagnosis/agent/app.py", "patch": "+ internal change"},
            {"filename": "services/ride/producer.go", "patch": "+ producer change"},
            {"filename": "services/dispatch/consumer.go", "patch": "+ consumer change"},
        ],
    }))

    result = collect_service_source_evidence(
        "ghcr.io/example/dispatch-service:v1.0.0-build.z.2.a1b2c3d4",
        client,
        service="dispatch-service",
    )

    assert [item["path"] for item in result["changed_files"]] == ["services/dispatch/consumer.go"]


def test_service_filter_runs_before_the_changed_file_limit():
    unrelated = [
        {"filename": f"incident-diagnosis/agent/file-{index}.py", "patch": "+ internal"}
        for index in range(20)
    ]
    client = FakeServicesClient(GitHubResult("available", {
        "sha": "a1b2c3d4ef", "commit": {"message": "monorepo build", "author": {}},
        "files": [*unrelated, {"filename": "services/dispatch/consumer.go", "patch": "+ relevant"}],
    }))

    result = collect_service_source_evidence(
        "ghcr.io/example/dispatch-service:v1.0.0-build.z.2.a1b2c3d4",
        client,
        service="dispatch-service",
    )

    assert [item["path"] for item in result["changed_files"]] == ["services/dispatch/consumer.go"]


def test_public_provenance_summary_never_contains_raw_patch_text():
    summary = summarize_provenance({
        "service": "dispatch-service",
        "diff": "top-level private diff",
        "causal_status": {"status": "recent_context", "reason_codes": ["no_failure_linkage"]},
        "dual": {
            "gitops": {"status": "unavailable", "reason": "no_deployed_configuration_diff"},
            "service_source": {
                "status": "found",
                "commit": {"sha": "abc123", "url": "https://example/abc123"},
                "changed_files": [{"path": "services/dispatch/consumer.go", "patch": "private source text"}],
            },
        },
    })

    encoded = str(summary)
    assert "private source text" not in encoded
    assert "top-level private diff" not in encoded
    assert summary["dual"]["service_source"]["changed_paths"] == ["services/dispatch/consumer.go"]


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


def test_old_deployed_source_change_with_exact_failure_identifier_is_causal():
    result = classify_causality(
        provenance={
            "gitops": {"status": "found", "changed_at": "2026-08-05T10:00:00Z"},
            "service_source": {
                "status": "found",
                "changed_files": [{"path": "services/ride/producer.go", "patch": "+ type = Payment.Completed.v3"}],
            },
        },
        candidate_service="ride-service",
        log_evidence={"status": "found", "service": "dispatch-service", "message": "unknown event type Payment.Completed.v3"},
        trace_handoff={"status": "correlated", "involved_services": ["ride-service", "dispatch-service"], "error_message": "unknown event type Payment.Completed.v3"},
        alert_started_at="2026-08-07T10:00:00Z",
        failure_predates=False,
    )

    assert result.status == "causal_candidate"
    assert "exact_failure_identifier" in result.reason_codes
    assert "Payment.Completed.v3" in result.matched_identifiers


def test_generic_config_value_matches_hostname_without_special_field_rule():
    result = classify_causality(
        provenance={
            "gitops": {
                "status": "found",
                "changed_at": "2026-08-07T09:00:00Z",
                "env_diff": [{"key": "CACHE_ENDPOINT", "old_value": "cache:6379", "new_value": "wrong-cache-host:6379"}],
            },
            "service_source": {"status": "unavailable"},
        },
        candidate_service="ride-service",
        log_evidence={"status": "found", "service": "ride-service", "message": "dial tcp: lookup wrong-cache-host: no such host"},
        trace_handoff={"status": "no_trace_id"},
        alert_started_at="2026-08-07T10:00:00Z",
        failure_predates=False,
    )

    assert result.status == "causal_candidate"
    assert "wrong-cache-host" in result.matched_identifiers


def test_live_environment_diff_matches_runtime_dns_failure_without_knowledge():
    result = classify_causality(
        provenance={"gitops": {"status": "unavailable"}, "service_source": {"status": "unavailable"}},
        candidate_service="ride-service",
        log_evidence={"status": "found", "service": "ride-service", "message": "lookup bad-host: no such host"},
        trace_handoff={"status": "no_trace_id"},
        alert_started_at="2026-08-10T12:00:00Z",
        failure_predates=False,
        template_diff={"changed_at": "2026-08-10T11:59:00Z", "env_diff": [{"key": "REDIS_ADDR", "old_value": "redis:6379", "new_value": "bad-host:6379"}]},
    )

    assert result.status == "causal_candidate"
    assert "direct_runtime_change" in result.reason_codes
    assert "bad-host" in result.matched_identifiers


def test_recent_unrelated_change_remains_context_only():
    result = classify_causality(
        provenance={
            "gitops": {"status": "found", "changed_at": "2026-08-07T09:55:00Z", "env_diff": [{"key": "LOG_LEVEL", "new_value": "debug"}]},
            "service_source": {"status": "found", "changed_files": [{"path": "services/ride/log.go", "patch": "+ debug logging"}]},
        },
        candidate_service="ride-service",
        log_evidence={"status": "found", "service": "ride-service", "message": "unknown event type Payment.Completed.v3"},
        trace_handoff={"status": "correlated", "involved_services": ["ride-service"]},
        alert_started_at="2026-08-07T10:00:00Z",
        failure_predates=False,
    )

    assert result.status == "recent_context"
    assert "no_failure_linkage" in result.reason_codes


def test_preexisting_failure_conflicts_with_matching_change():
    result = classify_causality(
        provenance={"gitops": {"status": "found", "changed_at": "2026-08-07T09:00:00Z", "env_diff": [{"key": "CACHE_ENDPOINT", "new_value": "wrong-cache-host"}]}, "service_source": {"status": "unavailable"}},
        candidate_service="ride-service",
        log_evidence={"status": "found", "service": "ride-service", "message": "lookup wrong-cache-host failed"},
        trace_handoff={"status": "no_trace_id"},
        alert_started_at="2026-08-07T10:00:00Z",
        failure_predates=True,
    )

    assert result.status == "conflicting"
    assert "failure_predates_change" in result.reason_codes


def test_deployed_identity_reads_current_workload_image():
    def read_deployment(service, namespace):
        assert (service, namespace) == ("ride-service", "vroom-dev")
        return {
            "spec": {"template": {"spec": {"containers": [
                {"name": "app", "image": "ghcr.io/example/ride:v1.0.0-build.z.114.dc213686"},
            ]}}}
        }

    result = collect_deployed_identity("ride-service", "vroom-dev", read_deployment)

    assert result == {
        "status": "found",
        "service": "ride-service",
        "namespace": "vroom-dev",
        "image": "ghcr.io/example/ride:v1.0.0-build.z.114.dc213686",
    }


def test_deployed_identity_is_unavailable_when_workload_has_no_image():
    result = collect_deployed_identity("ride-service", "vroom-dev", lambda *_: {"spec": {}})

    assert result == {"status": "unavailable", "reason": "workload_image_unavailable"}


def test_gitops_selector_matches_current_config_diff_without_field_special_case():
    commits = [{
        "sha": "gitops123456",
        "html_url": "https://github.example/commit/gitops123456",
        "commit": {"message": "test: update producer mode", "author": {"name": "Dev", "date": "2026-08-05T10:00:00Z"}},
        "files": [{
            "filename": "apps/ride/overlays/dev/patches/producer-mode.yaml",
            "patch": "- name: PRODUCER_MODE\n+  value: legacy\n+- name: PRODUCER_MODE\n+  value: strict",
        }],
    }]

    result = select_gitops_change(commits, {
        "env_diff": [{"key": "PRODUCER_MODE", "old_value": "legacy", "new_value": "strict"}],
        "image_changed": False,
    })

    assert result["status"] == "found"
    assert result["commit"]["sha"] == "gitops123456"
    assert result["env_diff"] == [{"key": "PRODUCER_MODE", "old_value": "legacy", "new_value": "strict"}]


def test_gitops_selector_does_not_pick_unrelated_recent_commit():
    commits = [{
        "sha": "recent123",
        "commit": {"message": "chore: log level", "author": {"name": "Dev", "date": "2026-08-07T09:59:00Z"}},
        "files": [{"filename": "apps/ride/overlays/dev/log.yaml", "patch": "+- name: LOG_LEVEL\n+  value: debug"}],
    }]

    result = select_gitops_change(commits, {
        "env_diff": [{"key": "PRODUCER_MODE", "old_value": "legacy", "new_value": "strict"}],
        "image_changed": False,
    })

    assert result == {"status": "unavailable", "reason": "no_matching_deployed_change"}
