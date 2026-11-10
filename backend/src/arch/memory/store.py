"""Small SQLite repository for conversations and approval-gated memory."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from arch.memory.context import MAX_MEMORY_CANDIDATE_CHARS
from arch.memory.migrations import MigrationRunner

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
        normalized = _candidate_content(content)
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
                (candidate_id, source_message_id, normalized, now, now),
            )
        return MemoryCandidate(candidate_id, source_message_id, normalized, "pending", now, now)

    def list_candidates(
        self, status: MemoryStatus | None = None, limit: int | None = None
    ) -> tuple[MemoryCandidate, ...]:
        """Return candidates without treating their existence as prompt eligibility."""
        if limit is not None and limit < 1:
            raise ValueError("Candidate limit must be positive.")
        query = (
            "SELECT id, source_message_id, content, status, created_at, updated_at "
            "FROM memory_candidates"
        )
        parameters: tuple[MemoryStatus | int, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (status,)
        query += " ORDER BY created_at DESC, id DESC"
        if limit is not None:
            query += " LIMIT ?"
            parameters += (limit,)
        with self._connect(readonly=True) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_memory_candidate(row) for row in rows)

    def edit_candidate(self, candidate_id: str, content: str, actor: str) -> MemoryCandidate:
        """Edit a pending candidate and retain its original wording in the audit trail."""
        replacement = _candidate_content(content)
        with self._connect() as connection:
            candidate = self._candidate_for_update(connection, candidate_id)
            if candidate.status != "pending":
                raise ValueError("Only pending memory candidates can be edited.")
            now = _now()
            connection.execute(
                "UPDATE memory_candidates SET content = ?, updated_at = ? WHERE id = ?",
                (replacement, now, candidate_id),
            )
            connection.execute(
                """
                INSERT INTO memory_edits(
                    candidate_id, previous_content, replacement_content, actor, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (candidate_id, candidate.content, replacement, actor, now),
            )
        return MemoryCandidate(
            candidate.id,
            candidate.source_message_id,
            replacement,
            candidate.status,
            candidate.created_at,
            now,
        )

    def decide_candidate(
        self, candidate_id: str, action: Literal["approved", "rejected", "deleted"], actor: str
    ) -> MemoryCandidate:
        """Apply one legal, audited state transition to a candidate."""
        with self._connect() as connection:
            candidate = self._candidate_for_update(connection, candidate_id)
            _validate_transition(candidate.status, action)
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
        return MemoryCandidate(
            candidate_id,
            candidate.source_message_id,
            candidate.content,
            action,
            candidate.created_at,
            now,
        )

    @staticmethod
    def _candidate_for_update(connection: sqlite3.Connection, candidate_id: str) -> MemoryCandidate:
        row = connection.execute(
            """
            SELECT id, source_message_id, content, status, created_at, updated_at
            FROM memory_candidates WHERE id = ?
            """,
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown memory candidate: {candidate_id}")
        return _memory_candidate(row)

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


def _memory_candidate(row: tuple[object, ...] | None) -> MemoryCandidate:
    if row is None:
        raise RuntimeError("SQLite memory candidate row was not found.")
    candidate_id, source_message_id, content, status, created_at, updated_at = row
    if not (
        isinstance(candidate_id, str)
        and isinstance(source_message_id, int)
        and isinstance(content, str)
        and status in ("pending", "approved", "rejected", "deleted")
        and isinstance(created_at, str)
        and isinstance(updated_at, str)
    ):
        raise RuntimeError("SQLite returned an invalid memory candidate row.")
    return MemoryCandidate(candidate_id, source_message_id, content, status, created_at, updated_at)


def _candidate_content(content: str) -> str:
    normalized = content.strip()
    if not normalized:
        raise ValueError("Memory candidate content cannot be empty.")
    if len(normalized) > MAX_MEMORY_CANDIDATE_CHARS:
        message = f"Memory candidate content cannot exceed {MAX_MEMORY_CANDIDATE_CHARS} characters."
        raise ValueError(message)
    return normalized


def _validate_transition(current: object, target: MemoryStatus) -> None:
    allowed: dict[str, set[MemoryStatus]] = {
        "pending": {"approved", "rejected"},
        "approved": {"deleted"},
        "rejected": set(),
        "deleted": set(),
    }
    if not isinstance(current, str) or target not in allowed.get(current, set()):
        raise ValueError(f"Illegal memory state transition: {current!r} -> {target!r}")
