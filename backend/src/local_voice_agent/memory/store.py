"""Small SQLite repository for conversations and approval-gated memory."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from local_voice_agent.memory.migrations import MigrationRunner

MessageRole = Literal["user", "assistant", "system"]
MemoryStatus = Literal["pending", "approved", "rejected", "deleted"]


@dataclass(frozen=True, slots=True)
class StoredMessage:
    """One persistent message associated with one local conversation."""

    id: int
    conversation_id: str
    utterance_id: str
    role: MessageRole
    content: str
    created_at: str


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """A user-reviewable fact candidate that is not trusted by default."""

    id: str
    source_message_id: int
    content: str
    status: MemoryStatus
    created_at: str
    updated_at: str


class MemoryStore:
    """Own SQLite connections while preserving explicit migration and approval boundaries."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def migrate(self) -> tuple[int, ...]:
        """Apply versioned schema changes before any read or write operation."""
        return MigrationRunner(self._database_path).migrate()

    def create_conversation(self) -> str:
        """Create and return an opaque local conversation identifier."""
        conversation_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO conversations(id, created_at) VALUES (?, ?)",
                (conversation_id, _now()),
            )
        return conversation_id

    def append_message(
        self, conversation_id: str, utterance_id: str, role: MessageRole, content: str
    ) -> StoredMessage:
        """Persist an already-authorized conversation turn without deriving memory from it."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages(conversation_id, utterance_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, utterance_id, role, content, _now()),
            )
            message_id = cursor.lastrowid
            if not isinstance(message_id, int):
                raise RuntimeError("SQLite did not return a message identifier.")
            row = connection.execute(
                """
                SELECT id, conversation_id, utterance_id, role, content, created_at
                FROM messages WHERE id = ?
                """,
                (message_id,),
            ).fetchone()
        return _stored_message(row)

    def list_messages(self, conversation_id: str, limit: int = 20) -> tuple[StoredMessage, ...]:
        """Return the newest bounded history in chronological order."""
        if limit < 1:
            raise ValueError("Message history limit must be positive.")
        with self._connect(readonly=True) as connection:
            rows = connection.execute(
                """
                SELECT id, conversation_id, utterance_id, role, content, created_at
                FROM (
                    SELECT id, conversation_id, utterance_id, role, content, created_at
                    FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
                """,
                (conversation_id, limit),
            ).fetchall()
        return tuple(_stored_message(row) for row in rows)

    def create_candidate(self, source_message_id: int, content: str) -> MemoryCandidate:
        """Create a pending candidate that cannot affect future agent context yet."""
        candidate_id = str(uuid.uuid4())
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_candidates(
                    id, source_message_id, content, status, created_at, updated_at
                )
                VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (candidate_id, source_message_id, content, now, now),
            )
        return MemoryCandidate(candidate_id, source_message_id, content, "pending", now, now)

    def decide_candidate(
        self, candidate_id: str, action: Literal["approved", "rejected", "deleted"], actor: str
    ) -> MemoryCandidate:
        """Apply one legal, audited state transition to a candidate."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT source_message_id, content, status, created_at, updated_at
                FROM memory_candidates WHERE id = ?
                """,
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown memory candidate: {candidate_id}")
            source_message_id, content, status, created_at, _ = row
            _validate_transition(status, action)
            now = _now()
            connection.execute(
                "UPDATE memory_candidates SET status = ?, updated_at = ? WHERE id = ?",
                (action, now, candidate_id),
            )
            connection.execute(
                """
                INSERT INTO memory_decisions(candidate_id, action, actor, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (candidate_id, action, actor, now),
            )
        return MemoryCandidate(candidate_id, source_message_id, content, action, created_at, now)

    def _connect(self, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            connection = sqlite3.connect(f"file:{self._database_path}?mode=ro", uri=True)
        else:
            connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _stored_message(row: tuple[object, ...] | None) -> StoredMessage:
    if row is None:
        raise RuntimeError("SQLite message row was not found.")
    message_id, conversation_id, utterance_id, role, content, created_at = row
    if not (
        isinstance(message_id, int)
        and isinstance(conversation_id, str)
        and isinstance(utterance_id, str)
        and role in ("user", "assistant", "system")
        and isinstance(content, str)
        and isinstance(created_at, str)
    ):
        raise RuntimeError("SQLite returned an invalid message row.")
    return StoredMessage(message_id, conversation_id, utterance_id, role, content, created_at)


def _validate_transition(current: object, target: MemoryStatus) -> None:
    allowed: dict[str, set[MemoryStatus]] = {
        "pending": {"approved", "rejected"},
        "approved": {"deleted"},
        "rejected": set(),
        "deleted": set(),
    }
    if not isinstance(current, str) or target not in allowed.get(current, set()):
        raise ValueError(f"Illegal memory state transition: {current!r} -> {target!r}")
