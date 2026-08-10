from evidence_projection import build_evidence_projection
from retrieval.signals import extract_canonical_signals


def test_signal_extractor_reads_the_neutral_projection():
    projection = build_evidence_projection(
        "KubePodContainerWaiting",
        {"waiting_reason": "ErrImagePull", "last_terminated_reason": "OOMKilled"},
    )

    assert extract_canonical_signals(projection) == frozenset({"ImagePullBackOff", "OOMKilled"})
