"""Tests for benchmark sample integrity helpers."""

import hashlib
from pathlib import Path

from local_voice_agent.benchmarks.samples import sha256_file


def test_sha256_file_matches_known_content(tmp_path: Path) -> None:
    """The downloader verifies exact bytes before accepting a benchmark sample."""
    sample = tmp_path / "sample.wav"
    sample.write_bytes(b"local voice benchmark")

    assert sha256_file(sample) == hashlib.sha256(b"local voice benchmark").hexdigest()
