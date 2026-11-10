"""Constrained local knowledge-inbox scanning and text extraction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

MAX_SOURCE_BYTES = 10 * 1024 * 1024
SUPPORTED_SUFFIXES = frozenset({".txt", ".md", ".py", ".json", ".yaml", ".yml", ".pdf"})


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    """One safely read inbox file with a repository-relative source identity."""

    path: str
    digest: str
    text: str


def scan_inbox(inbox: Path) -> tuple[KnowledgeDocument, ...]:
    """Read supported regular files below inbox, skipping unsafe or unreadable inputs."""
    root = inbox.resolve()
    documents: list[KnowledgeDocument] = []
    for candidate in sorted(inbox.rglob("*")):
        supported = candidate.suffix.lower() in SUPPORTED_SUFFIXES
        if candidate.is_symlink() or not candidate.is_file() or not supported:
            continue
        resolved = candidate.resolve()
        if root not in resolved.parents or candidate.stat().st_size > MAX_SOURCE_BYTES:
            continue
        try:
            raw = candidate.read_bytes()
            text = _extract_text(candidate, raw)
        except (OSError, PyPdfError, UnicodeDecodeError, ValueError):
            continue
        if text.strip():
            relative_path = candidate.relative_to(inbox).as_posix()
            documents.append(KnowledgeDocument(relative_path, _digest(raw), text))
    return tuple(documents)


def chunk_text(text: str, size: int = 1000, overlap: int = 150) -> tuple[str, ...]:
    """Create bounded overlapping chunks without splitting the input into an empty tail."""
    if size < 1 or overlap < 0 or overlap >= size:
        raise ValueError("Chunk overlap must be non-negative and smaller than chunk size.")
    normalized = " ".join(text.split())
    step = size - overlap
    return tuple(normalized[index : index + size] for index in range(0, len(normalized), step))


def _extract_text(path: Path, raw: bytes) -> str:
    if path.suffix.lower() == ".pdf":
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return raw.decode("utf-8")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
