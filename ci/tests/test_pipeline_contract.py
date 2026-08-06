from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_active_pipeline_builds_and_publishes_ride_image():
    pipeline = (ROOT / ".gitlab-ci.yml").read_text()
    assert "test-ride" in pipeline
    assert "build-push-ride" in pipeline
    assert "SERVICE_PATH: services/ride" in pipeline
    assert "needs: [test-ride]" in pipeline


def test_incident_agent_build_uses_configured_registry_mirror():
    dockerfile = (ROOT / "incident-diagnosis/agent/Dockerfile").read_text()
    assert 'ARG BASE_IMAGE_PREFIX=""' in dockerfile
    assert dockerfile.count("FROM ${BASE_IMAGE_PREFIX}python:3.12-slim") == 2
    assert "FROM python:3.12-slim" not in dockerfile
