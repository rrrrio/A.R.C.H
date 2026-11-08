"""Tests for safe local knowledge inbox ingestion."""

from pathlib import Path

import pytest

from local_voice_agent.rag.ingest import MAX_SOURCE_BYTES, chunk_text, scan_inbox


def test_scan_inbox_accepts_only_bounded_regular_supported_files(tmp_path: Path) -> None:
    """Inbox scanning ignores unsupported, oversize, and symlinked inputs."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "notes.md").write_text("本地知识", encoding="utf-8")
    (inbox / "ignore.bin").write_bytes(b"not indexed")
    (inbox / "large.txt").write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))
    (inbox / "broken.pdf").write_bytes(b"not a PDF")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (inbox / "linked.txt").symlink_to(outside)

    documents = scan_inbox(inbox)

    assert [(document.path, document.text) for document in documents] == [("notes.md", "本地知识")]


def test_chunk_text_bounds_overlap_and_arguments() -> None:
    """Chunking preserves bounded overlap and rejects invalid configuration."""
    assert chunk_text("abcdef", size=4, overlap=1) == ("abcd", "def")
    with pytest.raises(ValueError):
        chunk_text("text", size=4, overlap=4)
