"""Download the pinned Kokoro Chinese ONNX model and voice with hash checks."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, cast
from urllib.request import urlopen

from huggingface_hub import hf_hub_download

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = WORKSPACE_ROOT / "config" / "benchmark-baseline.json"
MODEL_ROOT = WORKSPACE_ROOT / "data" / "models" / "kokoro-zh"


def sha256_file(path: Path) -> str:
    """Return a SHA-256 digest for a downloaded model artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_voice_pack(url: str, destination: Path, expected_hash: str) -> None:
    """Download a release asset atomically and accept it only after hash validation."""
    if destination.is_file() and sha256_file(destination) == expected_hash:
        return
    with urlopen(url, timeout=60) as response:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := response.read(1024 * 1024):
                temporary.write(chunk)
    if sha256_file(temporary_path) != expected_hash:
        temporary_path.unlink(missing_ok=True)
        raise ValueError("Kokoro voice pack digest mismatch.")
    temporary_path.replace(destination)


def main() -> None:
    """Fetch and verify the exact model and voice declared in the baseline config."""
    baseline: Any = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    tts = baseline.get("tts", {}) if isinstance(baseline, dict) else {}
    values = (
        tts.get("model"),
        tts.get("revision"),
        tts.get("model_filename"),
        tts.get("model_sha256"),
        tts.get("voice_filename"),
        tts.get("voice_url"),
        tts.get("voice_sha256"),
    )
    if not all(isinstance(value, str) for value in values):
        raise SystemExit("The TTS baseline config is incomplete.")
    repo, revision, model_name, model_hash, voice_name, voice_url, voice_hash = cast(
        tuple[str, str, str, str, str, str, str], values
    )
    destination = MODEL_ROOT / revision
    destination.mkdir(parents=True, exist_ok=True)
    model_path = Path(
        hf_hub_download(
            repo_id=repo,
            filename=model_name,
            revision=revision,
            local_dir=destination,
        )
    )
    if sha256_file(model_path) != model_hash:
        raise SystemExit(f"TTS artifact digest mismatch: {model_name}")
    download_voice_pack(voice_url, destination / voice_name, voice_hash)
    (destination / "lva-provenance.json").write_text(
        json.dumps(
            {
                "source_repo": repo,
                "revision": revision,
                "model_filename": model_name,
                "model_sha256": model_hash,
                "voice_filename": voice_name,
                "voice_url": voice_url,
                "voice_sha256": voice_hash,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(destination)


if __name__ == "__main__":
    main()
