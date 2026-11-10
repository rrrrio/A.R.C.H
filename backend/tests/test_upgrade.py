"""Tests for the backup-first upgrade workflow."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from pytest import MonkeyPatch


def _load_script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "upgrade.py"
    specification = importlib.util.spec_from_file_location("upgrade", path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_upgrade_archives_before_applying_migrations(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    module = _load_script()
    events: list[str] = []
    archive = tmp_path / "data/backups/upgrade.tar.gz"

    class FakeSettings:
        data_dir = tmp_path / "data"
        database_path = tmp_path / "data/db/arch.sqlite3"

        def ensure_runtime_directories(self) -> None:
            events.append("directories")

    def create_backup(data_dir: Path, destination: Path) -> Path:
        assert data_dir == FakeSettings.data_dir
        assert destination.parent == FakeSettings.data_dir / "backups"
        events.append("backup")
        return archive

    class FakeStore:
        def __init__(self, database_path: Path) -> None:
            assert database_path == FakeSettings.database_path

        def migrate(self) -> tuple[int, ...]:
            assert events == ["directories", "backup"]
            events.append("migrate")
            return (5,)

    monkeypatch.setattr(module, "create_backup", create_backup)
    monkeypatch.setattr(module, "MemoryStore", FakeStore)

    result = module.upgrade(FakeSettings())

    assert result == (archive, (5,))
    assert events == ["directories", "backup", "migrate"]


def test_upgrade_reports_the_backup_path_when_migration_fails(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    module = _load_script()
    archive = tmp_path / "data/backups/upgrade.tar.gz"

    class FakeSettings:
        data_dir = tmp_path / "data"
        database_path = tmp_path / "data/db/arch.sqlite3"

        def ensure_runtime_directories(self) -> None:
            pass

    class FailingStore:
        def __init__(self, _: Path) -> None:
            pass

        def migrate(self) -> tuple[int, ...]:
            raise OSError("simulated migration failure")

    monkeypatch.setattr(module, "create_backup", lambda *_: archive)
    monkeypatch.setattr(module, "MemoryStore", FailingStore)

    with pytest.raises(RuntimeError, match=f"restore from backup archive: {archive}"):
        module.upgrade(FakeSettings())


def test_dry_run_prints_the_configured_backup_location(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_script()

    class FakeSettings:
        data_dir = tmp_path / "data" / "custom"

    monkeypatch.setattr(module, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(sys, "argv", ["upgrade.py"])

    module.main()

    assert str(FakeSettings.data_dir / "backups") in capsys.readouterr().out
