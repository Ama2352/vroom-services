import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


TOKENIZER_PATTERNS = (
    "config.json", "tokenizer.json", "tokenizer_config.json",
    "special_tokens_map.json", "vocab.txt", "spm.model",
)


@dataclass(frozen=True)
class ModelSpec:
    name: str
    repo_id: str
    revision: str
    onnx_file: str
    sha256: str
    max_length: int
    license: str = "apache-2.0"


def _snapshot_download(**kwargs):
    from huggingface_hub import snapshot_download

    return snapshot_download(**kwargs)


def load_manifest(path: Path | None = None) -> tuple[ModelSpec, ...]:
    manifest_path = path or Path(__file__).with_name("model_manifest.json")
    records = json.loads(manifest_path.read_text(encoding="utf-8"))
    return tuple(
        ModelSpec(
            name=record["name"],
            repo_id=record["repo_id"],
            revision=record["revision"],
            onnx_file=record["onnx_file"],
            sha256=record["sha256"],
            max_length=record["max_length"],
            license=record["license"],
        )
        for record in records
    )


def verify_sha256(artifact: Path, expected_sha256: str) -> Path:
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"checksum mismatch for {artifact}")
    return artifact


def ensure_model_artifact(spec: ModelSpec, cache_dir: Path) -> Path:
    cache_dir = Path(cache_dir)
    artifact = cache_dir / spec.onnx_file
    if artifact.is_file():
        try:
            return verify_sha256(artifact, spec.sha256).resolve()
        except ValueError:
            artifact.unlink()
            raise

    _snapshot_download(
        repo_id=spec.repo_id,
        revision=spec.revision,
        local_dir=cache_dir,
        allow_patterns=(*TOKENIZER_PATTERNS, spec.onnx_file),
    )
    try:
        return verify_sha256(artifact, spec.sha256).resolve()
    except ValueError:
        if artifact.is_file():
            artifact.unlink()
        raise
