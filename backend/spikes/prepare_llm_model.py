"""Download the exact Qwen3 GGUF baseline and verify its LFS digest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from huggingface_hub import hf_hub_download

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = WORKSPACE_ROOT / "config" / "benchmark-baseline.json"
MODEL_ROOT = WORKSPACE_ROOT / "data" / "models" / "qwen3"


def sha256_file(path: Path) -> str:
    """Return a lower-case SHA-256 digest without loading a model into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    """Resume or download the pinned GGUF, then persist verified provenance."""
    baseline: Any = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    llm = baseline.get("llm", {}) if isinstance(baseline, dict) else {}
    source_repo = llm.get("source_repo")
    revision = llm.get("revision")
    filename = llm.get("filename")
    expected_digest = llm.get("file_sha256")
    required_values = (source_repo, revision, filename, expected_digest)
    if not all(isinstance(value, str) for value in required_values):
        raise SystemExit(
            "The LLM baseline requires source_repo, revision, filename, and file_sha256."
        )

    destination = MODEL_ROOT / revision
    destination.mkdir(parents=True, exist_ok=True)
    model_path = Path(
        hf_hub_download(
            repo_id=source_repo,
            filename=filename,
            revision=revision,
            local_dir=destination,
        )
    )
    actual_digest = sha256_file(model_path)
    if actual_digest != expected_digest:
        raise SystemExit(
            f"GGUF digest mismatch: expected {expected_digest}, received {actual_digest}"
        )
    (destination / "lva-provenance.json").write_text(
        json.dumps(
            {
                "source_repo": source_repo,
                "revision": revision,
                "filename": filename,
                "file_sha256": actual_digest,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(model_path)


if __name__ == "__main__":
    main()
