"""Download and verify the pinned local MiniLM ONNX reranker artifact."""

import argparse
import json
from pathlib import Path

from huggingface_hub import snapshot_download

from retrieval.reranker import verify_sha256


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("retrieval/model_manifest.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.manifest.read_text(encoding="utf-8"))
    if isinstance(spec, list):
        spec = spec[0]
    args.output.mkdir(parents=True, exist_ok=True)
    artifact = args.output / spec["onnx_file"]
    if not artifact.is_file():
        snapshot_download(
            repo_id=spec["repo_id"], revision=spec["revision"],
            local_dir=args.output,
            allow_patterns=("config.json", "tokenizer.json", "tokenizer_config.json",
                            "special_tokens_map.json", "vocab.txt", spec["onnx_file"]),
        )
    verify_sha256(artifact, spec["sha256"])
    print(f"verified {artifact} ({artifact.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
