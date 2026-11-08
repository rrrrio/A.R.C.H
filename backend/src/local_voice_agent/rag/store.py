"""SQLite-backed, rebuildable local vector index for one user."""

from __future__ import annotations

import math
import sqlite3
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from local_voice_agent.memory.migrations import MigrationRunner


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    """One retrieved local chunk with an inspectable source path and score."""

    path: str
    content: str
    score: float


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
        return tuple(sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit])

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
