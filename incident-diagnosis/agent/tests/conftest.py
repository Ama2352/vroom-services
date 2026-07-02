import pytest

try:
    import fakeredis
    _FAKEREDIS_AVAILABLE = True
except ImportError:
    _FAKEREDIS_AVAILABLE = False


@pytest.fixture
def fake_rdb():
    if not _FAKEREDIS_AVAILABLE:
        pytest.skip("fakeredis not installed")
    return fakeredis.FakeRedis()


def kubectl_scale_responses(tool_name, args):
    """Returns realistic kubectl output for a scaled-to-zero service."""
    if tool_name == "get_pods":
        return "No resources found in vroom-dev namespace."
    if tool_name == "get_events":
        return "ScalingReplicaSet  ride-service-7d9f5  Scaled down replica set to 0"
    return "[no output]"


def kubectl_crashloop_responses(tool_name, args):
    """Returns realistic kubectl output for a crash-looping pod."""
    if tool_name == "get_pods":
        return "ride-service-abc123  0/1  CrashLoopBackOff  5  10m"
    if tool_name == "get_logs":
        return "OOMKilled: container exceeded memory limit (limit: 256Mi, usage: 259Mi)"
    return "[no output]"
