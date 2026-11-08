"""Approved benchmark sample download and integrity checks."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.request import urlopen

WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
MANIFEST_PATH = WORKSPACE_ROOT / "config" / "benchmark-samples.json"
SAMPLE_ROOT = WORKSPACE_ROOT / "data" / "benchmarks" / "samples"


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 digest for a local file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_sample(name: str, metadata: Mapping[str, Any]) -> Path:
    """Download one manifest sample atomically and reject checksum mismatches."""
    destination_name = metadata["destination"]
    expected_digest = metadata["sha256"]
    audio_url = metadata["audio_url"]
    if not (
        isinstance(destination_name, str)
        and isinstance(expected_digest, str)
        and isinstance(audio_url, str)
    ):
        raise ValueError(f"Sample {name} has invalid string metadata.")

    SAMPLE_ROOT.mkdir(parents=True, exist_ok=True)
    destination = SAMPLE_ROOT / destination_name
    if destination.is_file() and sha256_file(destination) == expected_digest:
        return destination

    with urlopen(audio_url, timeout=30) as response:
        with tempfile.NamedTemporaryFile(dir=SAMPLE_ROOT, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := response.read(1024 * 1024):
                temporary.write(chunk)
    if sha256_file(temporary_path) != expected_digest:
        temporary_path.unlink(missing_ok=True)
        raise ValueError(f"Checksum mismatch for {name}; removed incomplete download.")
    temporary_path.replace(destination)
    return destination


def prepare_manifest_samples() -> list[Path]:
    """Prepare every manifest sample and return its local destination path."""
    manifest: Any = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Benchmark sample manifest must be a JSON object.")
    destinations: list[Path] = []
    for name, metadata in manifest.items():
        if not isinstance(name, str) or not isinstance(metadata, dict):
            raise ValueError("Benchmark sample manifest entries must be objects.")
        destinations.append(download_sample(name, metadata))
    return destinations
