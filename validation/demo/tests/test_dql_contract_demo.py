from pathlib import Path


SCRIPT_PATHS = (
    Path(__file__).parents[4] / "vroom-infra" / "inject-poison-pill.sh",
    Path(__file__).parents[1] / "inject-poison-pill.sh",
)


def test_dlq_demo_uses_normal_ride_requests_not_redis_injection():
    canonical, wrapper = SCRIPT_PATHS
    source = canonical.read_text(encoding="utf-8")
    assert "EVENT_CONTRACT_VERSION=v2" in source
    assert "ROLLOUT_TIMEOUT=\"${ROLLOUT_TIMEOUT:-300s}\"" in source
    assert "/ride-service/v1/trips" in source
    assert "X-Correlation-ID" in source
    assert "XADD" not in source
    assert "redis-cli" not in source
    assert "kubectl scale deployment dispatch-service" not in source
    assert "exec bash" in wrapper.read_text(encoding="utf-8")


def test_dlq_demo_restores_the_healthy_contract():
    source = SCRIPT_PATHS[0].read_text(encoding="utf-8")
    assert "EVENT_CONTRACT_VERSION=v1" in source
    assert "trap" in source
    assert "kubectl rollout status deployment/ride-service" in source


def test_dlq_demo_waits_for_ingress_after_the_contract_rollout():
    source = SCRIPT_PATHS[0].read_text(encoding="utf-8")
    assert "wait_for_ride_endpoint" in source
    assert "Waiting for ride endpoint to accept requests" in source
