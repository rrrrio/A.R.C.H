"""Create and restore verified local A.R.C.H. data archives."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

MANIFEST = "manifest.json"
DATABASE = "database.sqlite3"
KNOWLEDGE = "knowledge"
CURRENT_DATABASE = "arch.sqlite3"
LEGACY_DATABASE = "local-voice-agent.sqlite3"


def create_backup(data_dir: Path, destination: Path) -> Path:
    """Write a compressed archive from a consistent database snapshot and inbox files."""
    database_directory = data_dir / "db"
    current_database = database_directory / CURRENT_DATABASE
    legacy_database = database_directory / LEGACY_DATABASE
    database = current_database if current_database.exists() else legacy_database
    inbox = data_dir / "knowledge" / "inbox"
    _reject_symlinked_data_paths(data_dir, database_directory, database, inbox.parent, inbox)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Backup destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        root = Path(temporary)
        entries: dict[str, str] = {}
        if database.is_file():
            snapshot = root / DATABASE
            with (
                sqlite3.connect(database) as source_connection,
                sqlite3.connect(snapshot) as target_connection,
            ):
                source_connection.backup(target_connection)
            entries[DATABASE] = _digest(snapshot)
        if inbox.is_dir():
            for source_file in inbox.rglob("*"):
                if source_file.is_symlink():
                    raise ValueError(f"Knowledge inbox contains a symbolic link: {source_file}")
                if source_file.is_file():
                    relative = source_file.relative_to(inbox)
                    target = root / KNOWLEDGE / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source_file, target)
                    entries[f"{KNOWLEDGE}/{relative.as_posix()}"] = _digest(target)
        (root / MANIFEST).write_text(
            json.dumps(
                {"version": 1, "created_at": datetime.now(UTC).isoformat(), "entries": entries},
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        with tarfile.open(destination, "w:gz") as archive:
            for path in root.rglob("*"):
                archive.add(path, arcname=path.relative_to(root), recursive=False)
    return destination


def restore_backup(archive_path: Path, data_dir: Path, *, replace: bool) -> None:
    """Restore a verified archive only into empty targets unless replacement is explicit."""
    with tempfile.TemporaryDirectory(dir=data_dir.parent) as temporary:
        root = Path(temporary)
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(set(names)) != len(names) or any(
                member.issym()
                or member.islnk()
                or not (member.isdir() or member.isfile())
                or not _safe_member(member.name)
                for member in members
            ):
                raise ValueError("Backup archive contains an unsafe path.")
            archive.extractall(root, filter="data")
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("version") != 1
            or not isinstance(manifest.get("entries"), dict)
        ):
            raise ValueError("Backup archive has an unsupported manifest.")
        expected_files = {MANIFEST}
        for name, digest in manifest["entries"].items():
            path = root / name
            if (
                not isinstance(name, str)
                or not isinstance(digest, str)
                or not _backup_entry_name(name)
                or not path.is_file()
                or _digest(path) != digest
            ):
                raise ValueError("Backup archive checksum verification failed.")
            expected_files.add(name)
        actual_files = {
            path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
        }
        if actual_files != expected_files:
            raise ValueError("Backup archive contains files not declared by its manifest.")
        database_directory = data_dir / "db"
        inbox_parent = data_dir / "knowledge"
        targets = (
            (root / DATABASE, database_directory / CURRENT_DATABASE),
            (root / KNOWLEDGE, data_dir / "knowledge" / "inbox"),
        )
        _reject_symlinked_data_paths(
            data_dir,
            database_directory,
            targets[0][1],
            inbox_parent,
            targets[1][1],
        )
        existing = [target for source, target in targets if source.exists() and target.exists()]
        if existing and not replace:
            raise FileExistsError(
                "Restore targets already exist; rerun with --replace after stopping A.R.C.H."
            )
        staged: list[tuple[Path, Path]] = []
        try:
            for source, target in targets:
                if source.exists():
                    staged.append((target, _stage_restore_source(source, target)))
        except OSError:
            for _, staged_path in staged:
                _remove_path(staged_path.parent)
            raise
        _replace_staged_targets(staged)


def _safe_member(name: str) -> bool:
    path = Path(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def _backup_entry_name(name: str) -> bool:
    return name == DATABASE or (
        name.startswith(f"{KNOWLEDGE}/") and _safe_member(name) and len(Path(name).parts) > 1
    )


def _reject_symlinked_data_paths(*paths: Path) -> None:
    if any(path.is_symlink() for path in paths):
        raise ValueError("Backup data paths cannot be symbolic links.")


def _stage_restore_source(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{target.name}.restore-", dir=target.parent))
    staged = staging_root / target.name
    if source.is_dir():
        shutil.copytree(source, staged)
    else:
        shutil.copyfile(source, staged)
    return staged


def _replace_staged_targets(staged_targets: list[tuple[Path, Path]]) -> None:
    replaced: list[tuple[Path, Path | None]] = []
    try:
        for target, staged in staged_targets:
            backup = _move_existing_target(target)
            try:
                staged.replace(target)
            except OSError:
                if backup is not None:
                    backup.replace(target)
                raise
            replaced.append((target, backup))
    except OSError:
        for target, backup in reversed(replaced):
            _remove_path(target)
            if backup is not None:
                backup.replace(target)
        raise
    finally:
        for _, staged in staged_targets:
            _remove_path(staged.parent)
        for _, backup in replaced:
            if backup is not None:
                _remove_path(backup.parent)


def _move_existing_target(target: Path) -> Path | None:
    if not target.exists():
        return None
    backup_root = Path(tempfile.mkdtemp(prefix=f".{target.name}.previous-", dir=target.parent))
    backup = backup_root / target.name
    target.replace(backup)
    return backup


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
