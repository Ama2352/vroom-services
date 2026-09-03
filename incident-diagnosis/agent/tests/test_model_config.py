from pathlib import Path

from retrieval.reranker import load_model_spec


def test_pinned_minilm_manifest_is_available_to_the_reranker():
    spec = load_model_spec(Path("retrieval/model_manifest.json"))

    assert spec.name == "minilm"
    assert spec.repo_id == "cross-encoder/ms-marco-MiniLM-L6-v2"
    assert spec.onnx_file == "onnx/model_quint8_avx2.onnx"
