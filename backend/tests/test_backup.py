"""Tests for verified A.R.C.H. database and knowledge backups."""

import io
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from arch.backup import MANIFEST, create_backup, restore_backup


def _write_source_data(data_dir: Path) -> None:
    database = data_dir / "db" / "arch.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records (value) VALUES ('preserve me')")
    inbox = data_dir / "knowledge" / "inbox" / "notes"
    inbox.mkdir(parents=True)
    (inbox / "plan.md").write_text("keep this knowledge", encoding="utf-8")


def test_backup_round_trip_restores_database_and_knowledge(tmp_path: Path) -> None:
    """A verified archive restores a consistent SQLite copy and inbox hierarchy."""
    source = tmp_path / "source-data"
    _write_source_data(source)
    archive = tmp_path / "backups" / "arch.tar.gz"

    assert create_backup(source, archive) == archive
    restore = tmp_path / "restore-data"
    restore_backup(archive, restore, replace=False)

    with sqlite3.connect(restore / "db" / "arch.sqlite3") as connection:
        assert connection.execute("SELECT value FROM records").fetchone() == ("preserve me",)
    assert (
        restore / "knowledge" / "inbox" / "notes" / "plan.md"
    ).read_text() == "keep this knowledge"


def test_backup_refuses_an_existing_destination(tmp_path: Path) -> None:
    """A timestamp collision cannot silently replace a prior recovery archive."""
    source = tmp_path / "source-data"
    _write_source_data(source)
    archive = tmp_path / "backups" / "arch.tar.gz"
    archive.parent.mkdir()
    archive.write_bytes(b"keep the existing backup")

    with pytest.raises(FileExistsError, match="already exists"):
        create_backup(source, archive)

    assert archive.read_bytes() == b"keep the existing backup"


def test_backup_refuses_a_dangling_destination_link(tmp_path: Path) -> None:
    """A dangling link cannot redirect a newly created archive outside the backup directory."""
    source = tmp_path / "source-data"
    _write_source_data(source)
    archive = tmp_path / "backups" / "arch.tar.gz"
    archive.parent.mkdir()
    outside = tmp_path / "outside.tar.gz"
    archive.symlink_to(outside)

    with pytest.raises(FileExistsError, match="already exists"):
        create_backup(source, archive)

    assert not outside.exists()


def test_backup_uses_the_legacy_database_when_the_current_path_is_absent(tmp_path: Path) -> None:
    """Legacy deployments archive their active SQLite data before migration creates a new file."""
    source = tmp_path / "source-data"
    database = source / "db" / "local-voice-agent.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE legacy_records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_records (value) VALUES ('keep the legacy data')")

    archive = create_backup(source, tmp_path / "arch.tar.gz")
    restore = tmp_path / "restore-data"
    restore_backup(archive, restore, replace=False)

    with sqlite3.connect(restore / "db" / "arch.sqlite3") as connection:
        assert connection.execute("SELECT value FROM legacy_records").fetchone() == (
            "keep the legacy data",
        )


def test_restore_requires_explicit_replacement_of_existing_data(tmp_path: Path) -> None:
    """Restore preserves current targets unless the operator requests replacement."""
    source = tmp_path / "source-data"
    _write_source_data(source)
    archive = create_backup(source, tmp_path / "arch.tar.gz")
    restore = tmp_path / "restore-data"
    _write_source_data(restore)
    (restore / "knowledge" / "inbox" / "stale.txt").write_text("replace me", encoding="utf-8")

    with pytest.raises(FileExistsError, match="--replace"):
        restore_backup(archive, restore, replace=False)

    restore_backup(archive, restore, replace=True)
    with sqlite3.connect(restore / "db" / "arch.sqlite3") as connection:
        assert connection.execute("SELECT value FROM records").fetchone() == ("preserve me",)
    assert not (restore / "knowledge" / "inbox" / "stale.txt").exists()


def test_restore_rejects_payload_not_declared_by_manifest(tmp_path: Path) -> None:
    """Checksum metadata cannot hide arbitrary extra files in a recovery archive."""
    archive = tmp_path / "unexpected.tar.gz"
    manifest = {"version": 1, "entries": {}}
    with tarfile.open(archive, "w:gz") as output:
        _add_bytes(output, MANIFEST, json.dumps(manifest).encode())
        _add_bytes(output, "unexpected.txt", b"not part of the backup")

    with pytest.raises(ValueError, match="not declared"):
        restore_backup(archive, tmp_path / "restore-data", replace=False)


def test_restore_rejects_unsafe_tar_member_before_writing_data(tmp_path: Path) -> None:
    """Path traversal members fail before a restore creates configured data targets."""
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        _add_bytes(output, "../outside.txt", b"unsafe")

    restore = tmp_path / "restore-data"
    with pytest.raises(ValueError, match="unsafe path"):
        restore_backup(archive, restore, replace=False)

    assert not restore.exists()


def test_restore_rejects_a_symlinked_data_target(tmp_path: Path) -> None:
    """A local symlink cannot redirect archive content outside the configured data directory."""
    source = tmp_path / "source-data"
    _write_source_data(source)
    archive = create_backup(source, tmp_path / "arch.tar.gz")
    restore = tmp_path / "restore-data"
    restore.mkdir()
    outside = tmp_path / "outside-db"
    (restore / "db").symlink_to(outside)

    with pytest.raises(ValueError, match="symbolic links"):
        restore_backup(archive, restore, replace=False)

    assert not outside.exists()


def test_restore_rolls_back_a_prior_target_when_a_later_swap_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed inbox swap leaves both existing targets unchanged after rollback."""
    source = tmp_path / "source-data"
    _write_source_data(source)
    archive = create_backup(source, tmp_path / "arch.tar.gz")
    restore = tmp_path / "restore-data"
    _write_source_data(restore)
    with sqlite3.connect(restore / "db" / "arch.sqlite3") as connection:
        connection.execute("UPDATE records SET value = 'keep the current database'")
    original_replace = Path.replace
    inbox = restore / "knowledge" / "inbox"

    def fail_inbox_swap(path: Path, target: Path) -> Path:
        if path.parent.name.startswith(".inbox.restore-") and target == inbox:
            raise OSError("simulated inbox swap failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_inbox_swap)

    with pytest.raises(OSError, match="simulated inbox swap failure"):
        restore_backup(archive, restore, replace=True)

    with sqlite3.connect(restore / "db" / "arch.sqlite3") as connection:
        assert connection.execute("SELECT value FROM records").fetchone() == (
            "keep the current database",
        )
    assert (inbox / "notes" / "plan.md").read_text() == "keep this knowledge"


def _add_bytes(output: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    output.addfile(info, io.BytesIO(content))
