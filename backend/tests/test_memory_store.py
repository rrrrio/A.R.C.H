"""Tests for versioned, approval-gated local conversation persistence."""

import sqlite3
from pathlib import Path

import pytest

from arch.memory.context import MAX_MEMORY_CANDIDATE_CHARS
from arch.memory.migrations import MIGRATIONS, Migration, MigrationRunner
from arch.memory.store import MemoryStore


def test_migrations_are_idempotent_and_backup_before_a_later_schema_change(tmp_path: Path) -> None:
    """An existing database is backed up before a pending migration can change it."""
    database_path = tmp_path / "db" / "voice.sqlite3"
    first = Migration(1, "first", "CREATE TABLE first_table(id INTEGER PRIMARY KEY);")
    second = Migration(2, "second", "CREATE TABLE second_table(id INTEGER PRIMARY KEY);")

    assert MigrationRunner(database_path, (first,)).migrate() == (1,)
    assert MigrationRunner(database_path, (first,)).migrate() == ()
    assert MigrationRunner(database_path, (first, second)).migrate() == (2,)

    backups = list(database_path.parent.glob("voice.*.before-migration.sqlite3"))
    assert len(backups) == 1


def test_first_migration_backs_up_an_existing_unversioned_database(tmp_path: Path) -> None:
    """A legacy SQLite file is recoverable even before it has migration metadata."""
    database_path = tmp_path / "db" / "voice.sqlite3"
    database_path.parent.mkdir()
    with sqlite3.connect(database_path) as legacy:
        legacy.execute("CREATE TABLE legacy_records(value TEXT NOT NULL)")
        legacy.execute("INSERT INTO legacy_records(value) VALUES ('preserve me')")

    first = Migration(1, "first", "CREATE TABLE first_table(id INTEGER PRIMARY KEY);")
    assert MigrationRunner(database_path, (first,)).migrate() == (1,)

    backup_path = next(database_path.parent.glob("voice.*.before-migration.sqlite3"))
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute("SELECT value FROM legacy_records").fetchone() == ("preserve me",)


def test_migration_upgrade_adds_edit_audit_table(tmp_path: Path) -> None:
    """A v1 database upgrades to the candidate-edit audit schema without rebuilding data."""
    database_path = tmp_path / "voice.sqlite3"
    assert MigrationRunner(database_path, (MIGRATIONS[0],)).migrate() == (1,)
    assert MigrationRunner(database_path).migrate() == (2, 3, 4, 5, 6, 7)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'memory_edits'"
        ).fetchone() == ("memory_edits",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_sources'"
        ).fetchone() == ("knowledge_sources",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'tool_approvals'"
        ).fetchone() == ("tool_approvals",)
        assert (
            connection.execute(
                "SELECT request_arguments_json FROM tool_approvals LIMIT 1"
            ).fetchall()
            == []
        )


def test_store_keeps_candidates_out_of_context_until_an_audited_approval(tmp_path: Path) -> None:
    """Only legal state transitions can make a memory candidate approved or deleted."""
    store = MemoryStore(tmp_path / "voice.sqlite3")
    assert store.migrate() == (1, 2, 3, 4, 5, 6, 7)
    conversation_id = store.create_conversation()
    user = store.append_message(conversation_id, "turn-1", "user", "我喜欢简洁回答")
    store.append_message(conversation_id, "turn-1", "assistant", "明白。")

    assert [message.content for message in store.list_messages(conversation_id)] == [
        "我喜欢简洁回答",
        "明白。",
    ]
    candidate = store.create_candidate(user.id, "用户偏好简洁回答")
    assert candidate.status == "pending"
    edited = store.edit_candidate(candidate.id, "用户偏好短回答", "administrator")
    assert edited.content == "用户偏好短回答"
    assert [entry.content for entry in store.list_candidates("pending")] == ["用户偏好短回答"]
    approved = store.decide_candidate(candidate.id, "approved", "administrator")
    assert approved.status == "approved"
    deleted = store.decide_candidate(candidate.id, "deleted", "administrator")
    assert deleted.status == "deleted"
    with pytest.raises(ValueError, match="Illegal memory state transition"):
        store.decide_candidate(candidate.id, "approved", "administrator")
    oversized_candidate = store.create_candidate(user.id, "待编辑候选")
    with pytest.raises(ValueError, match="cannot exceed"):
        store.edit_candidate(
            oversized_candidate.id, "x" * (MAX_MEMORY_CANDIDATE_CHARS + 1), "administrator"
        )
