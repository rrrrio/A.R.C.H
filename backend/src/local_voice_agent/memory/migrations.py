"""Versioned SQLite migrations for local user data."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Migration:
    """One ordered, append-only database schema change."""

    version: int
    name: str
    sql: str


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        1,
        "create conversation memory tables",
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            ended_at TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            utterance_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX messages_by_conversation ON messages(conversation_id, id);
        CREATE TABLE memory_candidates (
            id TEXT PRIMARY KEY,
            source_message_id INTEGER NOT NULL REFERENCES messages(id),
            content TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'deleted')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE memory_decisions (
            id INTEGER PRIMARY KEY,
            candidate_id TEXT NOT NULL REFERENCES memory_candidates(id),
            action TEXT NOT NULL CHECK (action IN ('approved', 'rejected', 'deleted')),
            actor TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """,
    ),
    Migration(
        2,
        "create memory edit audit table",
        """
        CREATE TABLE memory_edits (
            id INTEGER PRIMARY KEY,
            candidate_id TEXT NOT NULL REFERENCES memory_candidates(id),
            previous_content TEXT NOT NULL,
            replacement_content TEXT NOT NULL,
            actor TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """,
    ),
    Migration(
        3,
        "create local knowledge index tables",
        """
        CREATE TABLE knowledge_sources (
            path TEXT PRIMARY KEY,
            content_sha256 TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE knowledge_chunks (
            id INTEGER PRIMARY KEY,
            source_path TEXT NOT NULL REFERENCES knowledge_sources(path) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            content TEXT NOT NULL,
            embedding BLOB NOT NULL,
            dimensions INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(source_path, ordinal)
        );
        CREATE INDEX knowledge_chunks_by_source ON knowledge_chunks(source_path, ordinal);
        """,
    ),
    Migration(
        4,
        "create tool approval audit tables",
        """
        CREATE TABLE tool_approvals (
            id TEXT PRIMARY KEY,
            request_sha256 TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            argv_json TEXT NOT NULL,
            working_directory TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'consumed')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            decided_at TEXT,
            decided_by TEXT
        );
        CREATE INDEX tool_approvals_by_status_expiry ON tool_approvals(status, expires_at);
        CREATE TABLE tool_approval_events (
            id INTEGER PRIMARY KEY,
            approval_id TEXT NOT NULL REFERENCES tool_approvals(id),
            action TEXT NOT NULL CHECK (action IN ('created', 'approved', 'rejected', 'expired', 'consumed')),
            actor TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX tool_approval_events_by_approval ON tool_approval_events(approval_id, id);
        """,
    ),
    Migration(
        5,
        "store original tool approval arguments",
        """
        ALTER TABLE tool_approvals ADD COLUMN request_arguments_json TEXT;
        """,
    ),
)


class MigrationRunner:
    """Apply only known migrations and backup existing SQLite data first."""

    def __init__(self, database_path: Path, migrations: Sequence[Migration] = MIGRATIONS) -> None:
        self._database_path = database_path
        self._migrations = tuple(migrations)
        versions = [migration.version for migration in self._migrations]
        if versions != sorted(versions) or len(versions) != len(set(versions)):
            raise ValueError("Database migrations must have unique ascending versions.")

    def migrate(self) -> tuple[int, ...]:
        """Apply pending migrations transactionally and return their versions."""
        database_existed = self._database_path.exists()
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            pending = tuple(
                migration for migration in self._migrations if migration.version not in applied
            )
            if not pending:
                return ()
            if database_existed:
                self._backup(connection)
            now = datetime.now(UTC).isoformat()
            for migration in pending:
                try:
                    connection.executescript("BEGIN IMMEDIATE;\n" + migration.sql)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (migration.version, now),
                    )
                    connection.execute("COMMIT")
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
        return tuple(migration.version for migration in pending)

    def _backup(self, connection: sqlite3.Connection) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = self._database_path.with_name(
            f"{self._database_path.stem}.{timestamp}.before-migration.sqlite3"
        )
        suffix = 1
        while backup_path.exists():
            backup_path = self._database_path.with_name(
                f"{self._database_path.stem}.{timestamp}.{suffix}.before-migration.sqlite3"
            )
            suffix += 1
        with sqlite3.connect(backup_path) as backup:
            connection.backup(backup)
        return backup_path
