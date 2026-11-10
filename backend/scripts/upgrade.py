"""Create a recoverable archive, then apply pending A.R.C.H. database migrations."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from arch.backup import create_backup
from arch.memory.store import MemoryStore
from arch.settings import Settings, get_settings


def upgrade(settings: Settings) -> tuple[Path, tuple[int, ...]]:
    """Back up configured data before applying forward-only SQLite migrations."""
    settings.ensure_runtime_directories()
    archive = (
        settings.data_dir
        / "backups"
        / f"arch-before-upgrade-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.tar.gz"
    )
    archive = create_backup(settings.data_dir, archive)
    try:
        applied = MemoryStore(settings.database_path).migrate()
    except Exception as error:
        raise RuntimeError(f"Migration failed; restore from backup archive: {archive}") from error
    return archive, applied


def main() -> None:
    """Show the upgrade boundary unless the operator explicitly applies it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Create a backup and apply pending migrations."
    )
    arguments = parser.parse_args()
    settings = get_settings()
    if not arguments.apply:
        print("Dry run. No backup or migration was created.")
        print(
            "Apply will create "
            f"{settings.data_dir / 'backups' / 'arch-before-upgrade-<UTC-microseconds>.tar.gz'}, "
            "then migrate SQLite."
        )
        return
    archive, applied = upgrade(settings)
    print(f"Backup archive: {archive}")
    print(f"Applied migrations: {', '.join(str(version) for version in applied) or 'none'}")


if __name__ == "__main__":
    main()
