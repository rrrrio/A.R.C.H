"""SQLite-backed, rebuildable local vector index for one user."""

from __future__ import annotations

import math
import sqlite3
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from arch.memory.migrations import MigrationRunner


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    """One retrieved local chunk with an inspectable source path and score."""

    path: str
    content: str
    score: float


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    """One browser-visible index entry without document contents or embeddings."""

    path: str
    updated_at: str


class KnowledgeStore:
    """Persist sources and embeddings without a separately administered vector service."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def migrate(self) -> tuple[int, ...]:
        """Apply shared SQLite schema migrations before index work."""
        return MigrationRunner(self._database_path).migrate()

    def source_is_current(self, path: str, digest: str) -> bool:
        """Report whether an unchanged source already has a local index entry."""
        with self._connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT content_sha256 FROM knowledge_sources WHERE path = ?", (path,)
            ).fetchone()
        return bool(row == (digest,))

    def replace_source(
        self, path: str, digest: str, chunks: tuple[tuple[str, tuple[float, ...]], ...]
    ) -> None:
        """Atomically replace one source and all of its rebuildable chunk vectors."""
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("DELETE FROM knowledge_sources WHERE path = ?", (path,))
            connection.execute(
                """
                INSERT INTO knowledge_sources(path, content_sha256, parser_version, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (path, digest, "v1", now),
            )
            for ordinal, (content, vector) in enumerate(chunks):
                connection.execute(
                    """
                    INSERT INTO knowledge_chunks(
                        source_path, ordinal, content, embedding, dimensions, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (path, ordinal, content, _encode(vector), len(vector), now),
                )

    def remove_sources_except(self, paths: set[str]) -> None:
        """Drop index entries for inbox files that no longer exist."""
        with self._connect() as connection:
            rows = connection.execute("SELECT path FROM knowledge_sources").fetchall()
            for (path,) in rows:
                if isinstance(path, str) and path not in paths:
                    connection.execute("DELETE FROM knowledge_sources WHERE path = ?", (path,))

    def search(self, query: tuple[float, ...], limit: int = 4) -> tuple[KnowledgeHit, ...]:
        """Score every local chunk by cosine similarity; suitable for the single-user corpus."""
        if limit < 1:
            raise ValueError("Knowledge search limit must be positive.")
        hits = self._vector_hits(query)
        return tuple(sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit])

    def search_hybrid(
        self, query: tuple[float, ...], text: str, limit: int = 4
    ) -> tuple[KnowledgeHit, ...]:
        """Combine local vector similarity and lightweight keyword overlap for one query."""
        if limit < 1:
            raise ValueError("Knowledge search limit must be positive.")
        keywords = _keywords(text)
        hits = self._vector_hits(query)
        ranked = (
            KnowledgeHit(
                hit.path,
                hit.content,
                0.7 * hit.score + 0.3 * _keyword_score(keywords, hit.content),
            )
            for hit in hits
        )
        return tuple(sorted(ranked, key=lambda hit: hit.score, reverse=True)[:limit])

    def index_status(self) -> tuple[int, str | None]:
        """Return indexed source count and newest successful source update time."""
        with self._connect(readonly=True) as connection:
            count, newest = connection.execute(
                "SELECT COUNT(*), MAX(updated_at) FROM knowledge_sources"
            ).fetchone()
        return int(count), newest if isinstance(newest, str) else None

    def list_sources(self, query: str = "") -> tuple[KnowledgeSource, ...]:
        """Return locally indexed source metadata for the authenticated archive view."""
        with self._connect(readonly=True) as connection:
            rows = connection.execute(
                "SELECT path, updated_at FROM knowledge_sources "
                "WHERE path LIKE ? ORDER BY updated_at DESC, path ASC",
                (f"%{query}%",),
            ).fetchall()
        return tuple(
            KnowledgeSource(path, updated_at)
            for path, updated_at in rows
            if isinstance(path, str) and isinstance(updated_at, str)
        )

    def _vector_hits(self, query: tuple[float, ...]) -> list[KnowledgeHit]:
        """Load compatible vectors for the intentionally small, single-user corpus."""
        with self._connect(readonly=True) as connection:
            rows = connection.execute(
                "SELECT source_path, content, embedding, dimensions FROM knowledge_chunks"
            ).fetchall()
        hits = [
            KnowledgeHit(path, content, _cosine(query, _decode(blob, dimensions)))
            for path, content, blob, dimensions in rows
            if isinstance(path, str)
            and isinstance(content, str)
            and isinstance(blob, bytes)
            and isinstance(dimensions, int)
            and dimensions == len(query)
        ]
        return hits

    def _connect(self, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            connection = sqlite3.connect(f"file:{self._database_path}?mode=ro", uri=True)
        else:
            connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _encode(vector: tuple[float, ...]) -> bytes:
    if not vector:
        raise ValueError("Knowledge embeddings cannot be empty.")
    return struct.pack(f"<{len(vector)}f", *vector)


def _decode(blob: bytes, dimensions: int) -> tuple[float, ...]:
    if dimensions < 1 or len(blob) != dimensions * 4:
        raise ValueError("Stored knowledge embedding is invalid.")
    return struct.unpack(f"<{dimensions}f", blob)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    left_norm = sum(value * value for value in left)
    right_norm = sum(value * value for value in right)
    denominator = math.sqrt(left_norm * right_norm)
    if denominator == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


def _keywords(text: str) -> frozenset[str]:
    """Build a dependency-free token set that handles words and Chinese character pairs."""
    normalized = text.casefold()
    words = {word for word in normalized.split() if len(word) > 1}
    han = "".join(character for character in normalized if "\u4e00" <= character <= "\u9fff")
    pairs = {han[index : index + 2] for index in range(len(han) - 1)}
    return frozenset(words | pairs)


def _keyword_score(keywords: frozenset[str], content: str) -> float:
    """Return the fraction of query tokens occurring in one chunk."""
    if not keywords:
        return 0.0
    normalized = content.casefold()
    return sum(keyword in normalized for keyword in keywords) / len(keywords)
