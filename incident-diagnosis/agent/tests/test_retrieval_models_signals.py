from retrieval.models import RankedCandidate, RetrievalDocument, RetrievalResult
from retrieval.signals import (
    extract_canonical_signals,
    select_unique_signal,
    serialize_incident,
    serialize_routed_incident,
    serialize_routed_reranker_query,
)
from routing import RoutingDecision


def _document(source="history"):
    return RetrievalDocument(
        knowledge_key="image_pull",
        source=source,
        source_id="history-1" if source == "history" else "image_pull",
        trigger="ImagePullBackOff",
        conclusive=False,
        root_cause_pattern="The image tag or registry credentials are invalid.",
        fix_action="Correct the image reference or registry secret.",
        document_text="manifest tag not found invalid image reference",
        context_notes="A bad GitOps image tag caused this occurrence.",
    )


def test_normal_metadata_hides_scores():
    candidate = RankedCandidate(_document(), bm25_score=7.5, matched_terms=("manifest",))
    result = RetrievalResult.accepted_advisory(
        candidate, reranker_score=2.4, corpus_version=7,
    )
    assert result.to_api_dict(debug=False) == {
        "mode": "reranked_advisory",
        "accepted": True,
        "knowledge_key": "image_pull",
        "source": "knowledge_with_history",
    }


def test_none_metadata_has_no_knowledge_key():
    result = RetrievalResult.none(corpus_version=7)
    assert result.to_api_dict(debug=False) == {
        "mode": "none", "accepted": False, "source": None,
    }


def test_debug_metadata_contains_retrieval_diagnostics():
    candidate = RankedCandidate(_document(), bm25_score=7.5, matched_terms=("manifest",))
    result = RetrievalResult.accepted_advisory(
        candidate, reranker_score=2.4, corpus_version=7,
        ranking=(("image_pull", 7.5, 2.4),),
    )
    payload = result.to_api_dict(debug=True)
    assert payload["debug"]["corpus_version"] == 7
    assert payload["debug"]["acceptance_floor"] == 1.1961885690689087
    assert payload["debug"]["ranking"][0]["knowledge_key"] == "image_pull"


def test_signal_extractor_accumulates_instead_of_first_match_wins():
    facts = {
        "init_last_terminated_reason": "OOMKilled",
        "waiting_reason": "ErrImagePull",
        "last_terminated_reason": "Error",
        "pods_available": 0,
        "pods_desired": 1,
        "event_reason": "Failed",
        "dependency": {
            "name": "postgres", "pods_available": 0, "pods_desired": 1,
            "waiting_reason": "CrashLoopBackOff",
        },
    }
    assert extract_canonical_signals(facts) == frozenset({
        "Init:OOMKilled", "ImagePullBackOff", "Error", "ZeroReplicas", "Failed",
        "Dependency:postgres:CrashLoopBackOff", "Dependency:postgres:Unhealthy",
    })


def test_unknown_termination_is_not_a_signal():
    assert extract_canonical_signals({"last_terminated_reason": "Unknown"}) == frozenset()


def test_reflection_uses_trigger_only_when_signal_is_unique():
    assert select_unique_signal({"waiting_reason": "CrashLoopBackOff"}) == "CrashLoopBackOff"
    assert select_unique_signal({
        "waiting_reason": "CrashLoopBackOff", "pods_available": 0, "pods_desired": 1,
    }) == ""


def test_incident_serialization_labels_rich_existing_evidence():
    text = serialize_incident("KubePodContainerWaiting", {
        "waiting_reason": "ImagePullBackOff",
        "log_error": "manifest tag not found",
        "event_reason": "Failed",
        "event_message": "registry rejected the pull",
        "dependency": {"name": "registry", "pods_available": 1, "pods_desired": 1},
        "template_diff": {
            "env_diff": [{"key": "IMAGE_TAG", "old_value": "v1", "new_value": "bad"}],
            "old_image": "ride:v1", "new_image": "ride:bad",
        },
    })
    assert "alert_name: KubePodContainerWaiting" in text
    assert "waiting_reason: ImagePullBackOff" in text
    assert "log_error: manifest tag not found" in text
    assert "dependency_name: registry" in text
    assert "template_env_key: IMAGE_TAG" in text
    assert "template_new_image: ride:bad" in text


def test_routed_serializers_prioritize_primary_for_bm25_and_keep_context_for_minilm():
    decision = RoutingDecision(
        incident_kind="dlq",
        evidence_chain={},
        primary_signals=(
            "log_evidence.message: unknown event type Trip.Requested.v2",
        ),
        secondary_signals=("k8s_state.event_reason: Unhealthy",),
        reason_codes=("explicit_incident_kind",),
    )

    lexical = serialize_routed_incident(decision)
    semantic = serialize_routed_reranker_query(decision)

    assert "unknown event type" in lexical
    assert "Unhealthy" not in lexical
    assert semantic.index("unknown event type") < semantic.index("Unhealthy")
    assert "incident_kind: dlq" in lexical
    assert "Primary incident evidence:" in semantic
    assert "Secondary context:" in semantic


def test_routed_bm25_serializer_falls_back_to_secondary_when_primary_is_empty():
    decision = RoutingDecision(
        incident_kind="generic",
        evidence_chain={},
        primary_signals=(),
        secondary_signals=("k8s_state.waiting_reason: CrashLoopBackOff",),
        reason_codes=("generic_fallback",),
    )

    lexical = serialize_routed_incident(decision)

    assert "CrashLoopBackOff" in lexical
