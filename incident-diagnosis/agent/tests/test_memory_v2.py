import memory


def test_example_evidence_is_immutable(fake_rdb):
    memory.store_knowledge_v2(fake_rdb, {
        "knowledge_key": "unsupported_event_contract",
        "diagnosis_cause": "Producer and consumer use incompatible event contracts.",
        "remediation": "Align the event contract before replaying the DLQ.",
    })
    example_id = memory.store_example_v2(fake_rdb, {
        "knowledge_key": "unsupported_event_contract",
        "fingerprint": "fp-1",
        "evidence": {"alert_name": "DLQEventsDetected", "log_error": "unknown event"},
        "exact_reusable": True,
        "approved_by": "operator",
    })

    assert memory.get_example_v2(fake_rdb, example_id)["evidence"]["log_error"] == "unknown event"
    assert memory.update_example_v2(fake_rdb, example_id, {"exact_reusable": False}) is True
    assert memory.get_example_v2(fake_rdb, example_id)["exact_reusable"] is False


def test_hints_are_global_and_deduplicated(fake_rdb):
    first = memory.store_hint_v2(fake_rdb, "DNS lookup failure")
    second = memory.store_hint_v2(fake_rdb, " dns   lookup failure ")
    assert first == second
    memory.link_knowledge_hints_v2(fake_rdb, "invalid_dependency_address", [first])
    assert memory.search_hints_v2(fake_rdb, "lookup")[0]["hint_id"] == first
