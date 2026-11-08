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
