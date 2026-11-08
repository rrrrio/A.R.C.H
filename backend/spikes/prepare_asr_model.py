"""Download the exact Faster-Whisper baseline model and verify its LFS digest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from huggingface_hub import snapshot_download

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = WORKSPACE_ROOT / "config" / "benchmark-baseline.json"
MODEL_ROOT = WORKSPACE_ROOT / "data" / "models" / "faster-whisper"


def sha256_file(path: Path) -> str:
    """Return a lower-case SHA-256 digest without loading a model into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    """Resume or download the pinned model, then persist verified provenance."""
    baseline: Any = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    asr = baseline.get("asr", {}) if isinstance(baseline, dict) else {}
    model = asr.get("model")
    revision = asr.get("revision")
    expected_digest = asr.get("model_bin_sha256")
    if not all(isinstance(value, str) for value in (model, revision, expected_digest)):
        raise SystemExit("The ASR baseline requires model, revision, and model_bin_sha256 strings.")

    destination = MODEL_ROOT / revision
    destination.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=model, revision=revision, local_dir=destination)
    model_bin = destination / "model.bin"
    actual_digest = sha256_file(model_bin)
    if actual_digest != expected_digest:
        raise SystemExit(
            f"model.bin digest mismatch: expected {expected_digest}, received {actual_digest}"
        )
    (destination / "lva-provenance.json").write_text(
        json.dumps(
            {
                "model": model,
                "revision": revision,
                "model_bin_sha256": actual_digest,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(destination)


if __name__ == "__main__":
    main()
