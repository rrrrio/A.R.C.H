"""Prepare the pinned Qwen3-TTS cloning artifacts only after explicit operator approval."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from huggingface_hub import snapshot_download

from arch.settings import Settings, get_settings

MODEL_REPOSITORY = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
MODEL_REVISION = "c27fe8aa05b732b1376d0f6a1e522fbccb84abbd"
QWEN_TTS_VERSION = "0.1.1"
ARTIFACT_HASHES = {
    "model.safetensors": "180b3b10eb1c9f1b4db7806d5475bae3071c0243c299d49926bab1da3b6946f6",
    "speech_tokenizer/model.safetensors": (
        "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"
    ),
}


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of one local model payload."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_root(settings: Settings) -> Path:
    """Return the ignored, data-confined directory for the pinned Qwen checkpoint."""
    return settings.data_dir / "models" / "qwen3-tts" / MODEL_REVISION


def runtime_root(settings: Settings) -> Path:
    """Return the isolated local Python environment for the optional Qwen runtime."""
    return settings.data_dir / "runtime" / "qwen-tts"


def verify_artifacts(destination: Path) -> None:
    """Reject a snapshot unless each published large-model checksum matches exactly."""
    for relative_path, expected_hash in ARTIFACT_HASHES.items():
        artifact = destination / relative_path
        if not artifact.is_file() or sha256_file(artifact) != expected_hash:
            raise RuntimeError(f"Qwen TTS artifact digest mismatch: {relative_path}")


def prepare(settings: Settings) -> tuple[Path, Path]:
    """Download, verify, and isolate the optional Qwen runtime after explicit apply."""
    settings.ensure_runtime_directories()
    destination = model_root(settings)
    provenance = destination / "arch-provenance.json"
    if destination.exists() and not provenance.is_file():
        raise RuntimeError(
            "Qwen TTS destination already exists without verified provenance; remove it manually "
            "after review before preparing again."
        )
    if not provenance.is_file():
        snapshot_download(
            repo_id=MODEL_REPOSITORY,
            revision=MODEL_REVISION,
            local_dir=destination,
        )
        verify_artifacts(destination)
        provenance.write_text(
            json.dumps(
                {
                    "source_repo": MODEL_REPOSITORY,
                    "revision": MODEL_REVISION,
                    "license": "Apache-2.0",
                    "qwen_tts_version": QWEN_TTS_VERSION,
                    "artifacts": ARTIFACT_HASHES,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    else:
        verify_artifacts(destination)

    environment = runtime_root(settings)
    python = environment / "bin" / "python"
    if not python.is_file():
        subprocess.run(["uv", "venv", str(environment)], check=True)
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), f"qwen-tts=={QWEN_TTS_VERSION}"],
            check=True,
        )
    return destination, environment


def main() -> None:
    """Print a no-write plan by default; perform preparation only with ``--apply``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Download and provision the isolated runtime."
    )
    arguments = parser.parse_args()
    settings = get_settings()
    destination = model_root(settings)
    environment = runtime_root(settings)
    if not arguments.apply:
        print("Dry run. No model download or runtime environment was created.")
        print(f"Model: {MODEL_REPOSITORY}@{MODEL_REVISION}")
        print(f"Destination: {destination}")
        print(f"Runtime: {environment}")
        print("Apply verifies published SHA-256 values before writing provenance.")
        return
    model, runtime = prepare(settings)
    print(f"Verified model: {model}")
    print(f"Isolated runtime: {runtime}")
    print(f"Provenance: {model / 'arch-provenance.json'}")


if __name__ == "__main__":
    main()
