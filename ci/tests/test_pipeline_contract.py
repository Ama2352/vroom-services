from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_active_pipeline_builds_and_publishes_ride_image():
    pipeline = (ROOT / ".gitlab-ci.yml").read_text()
    assert "test-ride" in pipeline
    assert "build-push-ride" in pipeline
    assert "SERVICE_PATH: services/ride" in pipeline
    assert "needs: [test-ride]" in pipeline
