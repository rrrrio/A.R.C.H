"""Content-addressed cache identity for fixed-video MuseTalk preprocessing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AvatarCacheIdentity:
    """Every input that changes fixed-video preprocessing output."""

    source_sha256: str
    musetalk_revision: str
    fps: int
    bbox_shift: int
    extra_margin: int
    parsing_mode: str

    @property
    def key(self) -> str:
        """Return a stable directory key without exposing the source path."""
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def write_manifest(self, directory: Path) -> None:
        """Create a manifest once; never replace incompatible cached artifacts."""
        directory.mkdir(parents=True, exist_ok=True)
        manifest = directory / "arch-avatar-manifest.json"
        contents = json.dumps(asdict(self), sort_keys=True, indent=2) + "\n"
        if manifest.exists() and manifest.read_text(encoding="utf-8") != contents:
            raise RuntimeError("Avatar cache manifest does not match the requested source.")
        manifest.write_text(contents, encoding="utf-8")


def sha256_file(source: Path) -> str:
    """Hash the operator-provided video without trusting its name or metadata."""
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
