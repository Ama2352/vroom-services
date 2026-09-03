from collector import EvidenceCollector, HttpObservationClient
from config import Settings


class FakeClient:
    def __init__(self, **observations):
        self.observations = observations
        self.scopes = []

    def collect(self, service, namespace):
        self.scopes.append((service, namespace))
        return self.observations


def test_collector_keeps_available_observations_when_logs_are_missing():
    client = FakeClient(metrics={"triggering_metric": "up=0"}, logs=None)

    result = EvidenceCollector(client).collect({"service": "orders"})

    assert result.raw["metrics"] == {"triggering_metric": "up=0"}
    assert result.missing == ("logs",)


def test_collector_scopes_requests_to_the_alert_service_and_namespace():
    client = FakeClient(metrics={})

    EvidenceCollector(client).collect({"service": "orders", "namespace": "shop"})

    assert client.scopes == [("orders", "shop")]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, *, params, headers=None, timeout):
        self.calls.append((url, params, headers, timeout))
        if url == "http://prometheus":
            reason = "CrashLoopBackOff" if "waiting_reason" in params["query"] else "Error"
            return FakeResponse({"data": {"result": [{"metric": {"reason": reason}, "value": [0, "1"]}]}})
        if url == "http://loki":
            return FakeResponse({"data": {"result": [{"values": [["0", "connection refused"]]}]}})
        if url == "http://tempo/api/search":
            return FakeResponse({"traces": [{"rootServiceName": "orders", "rootTraceName": "GET /checkout"}]})
        if url.endswith("/tools/events-json"):
            return FakeResponse({"events": [{"reason": "BackOff", "message": "restart loop"}]})
        if url.endswith("/tools/workload-revisions"):
            return FakeResponse({"current": {"orders": {"env": {"REDIS_ADDR": "redis-new"}}},
                                 "previous": {"orders": {"env": {"REDIS_ADDR": "redis-old"}}}})
        raise AssertionError(f"unexpected URL: {url}")


def test_http_client_collects_only_scoped_source_observations():
    settings = Settings(
        redis_url="redis://redis:6379/0", prometheus_url="http://prometheus",
        loki_url="http://loki", tempo_url="http://tempo", kubectl_executor_url="http://executor",
        kubectl_executor_api_key="secret", groq_api_key="", openrouter_api_key="",
    )
    session = FakeSession()

    raw = HttpObservationClient(settings, session=session, now_seconds=lambda: 60).collect("orders", "shop")

    assert raw["metrics"]["waiting_reason"] == "CrashLoopBackOff"
    assert raw["metrics"]["last_terminated_reason"] == "Error"
    assert raw["logs"] == {"message": "connection refused"}
    assert raw["traces"]["error_service"] == "orders"
    assert raw["kubernetes"]["event_reason"] == "BackOff"
    assert raw["configuration"]["changes"][0]["path"] == "containers.orders.env.REDIS_ADDR"
    assert all(call[1].get("namespace") == "shop" for call in session.calls if "namespace" in call[1])
