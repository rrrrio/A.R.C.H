"""Preview or remove A.R.C.H. generated files while preserving user data by default."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def uninstall_targets(project_root: Path, *, remove_data: bool) -> tuple[Path, ...]:
    """Return the exact generated paths that an uninstall may remove."""
    targets = (
        project_root / "frontend" / "dist",
        project_root / "deploy" / "caddy-data",
        project_root / "deploy" / "caddy-config",
    )
    return (*targets, project_root / "data") if remove_data else targets


def apply_uninstall(project_root: Path, targets: tuple[Path, ...]) -> tuple[Path, ...]:
    """Remove listed paths only when their resolved parents remain inside the project root."""
    resolved_root = project_root.resolve()
    removed: list[Path] = []
    for target in targets:
        resolved_parent = target.parent.resolve()
        if resolved_parent != resolved_root and resolved_root not in resolved_parent.parents:
            raise ValueError(f"Uninstall target escapes the project root: {target}")
        if target.is_symlink() or target.is_file():
            target.unlink(missing_ok=True)
            removed.append(target)
        elif target.is_dir():
            shutil.rmtree(target)
            removed.append(target)
    return tuple(removed)


def main() -> None:
    """Print an exact dry-run plan unless the operator explicitly requests deletion."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Remove the listed generated paths.")
    parser.add_argument(
        "--remove-data",
        action="store_true",
        help="Include data/ in the removal plan; requires --apply to delete it.",
    )
    arguments = parser.parse_args()
    if arguments.remove_data and not arguments.apply:
        parser.error("--remove-data requires --apply")

    targets = uninstall_targets(PROJECT_ROOT, remove_data=arguments.remove_data)
    if not arguments.apply:
        print("Dry run. No files were removed.")
        print("Would remove:")
        for target in targets:
            print(f"- {target}")
        if not arguments.remove_data:
            print(f"Would preserve: {PROJECT_ROOT / 'data'}")
        return

    for target in apply_uninstall(PROJECT_ROOT, targets):
        print(f"Removed: {target}")


if __name__ == "__main__":
    main()
