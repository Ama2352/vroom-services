from retrieval.bm25 import BM25Index
from retrieval.models import RetrievalDocument
from evidence_projection import build_evidence_projection


def make_document(key, text, source="knowledge", source_id=None):
    return RetrievalDocument(
        knowledge_key=key,
        source=source,
        source_id=source_id or key,
        trigger="",
        conclusive=False,
        root_cause_pattern=f"{key} root cause",
        fix_action=f"fix {key}",
        document_text=text,
        context_notes="history context" if source == "history" else "",
    )


def test_bm25_ranks_rare_incident_terms():
    docs = (
        make_document("oom", "exit memory limit OOMKilled"),
        make_document("image_pull", "manifest tag not found invalid image reference"),
    )
    projection = build_evidence_projection("KubePodContainerWaiting", {
        "waiting_reason": "ImagePullBackOff", "log_error": "manifest tag not found",
    })
    ranked = BM25Index(docs).search(projection.lexical_text(), limit=8)
    assert ranked[0].knowledge_key == "image_pull"
    assert ranked[0].bm25_score > 0


def test_bm25_collapses_multiple_documents_by_knowledge_key():
    docs = (
        make_document("image_pull", "invalid image", source="knowledge", source_id="image_pull"),
        make_document("image_pull", "manifest tag not found", source="history", source_id="h1"),
        make_document("oom", "memory limit exceeded", source="knowledge", source_id="oom"),
    )
    ranked = BM25Index(docs).search("manifest tag memory limit", limit=8)
    assert [c.knowledge_key for c in ranked].count("image_pull") == 1


def test_bm25_returns_no_zero_overlap_documents():
    index = BM25Index((make_document("oom", "memory limit exceeded"),))
    assert index.search("certificate expired", limit=8) == ()
