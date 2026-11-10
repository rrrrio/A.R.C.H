"""Tests for the explicit, data-preserving uninstall helper."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    """Load the standalone uninstall script without invoking its command-line entry point."""
    script_path = Path(__file__).parents[1] / "scripts" / "uninstall.py"
    specification = importlib.util.spec_from_file_location("uninstall", script_path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_uninstall_targets_preserve_data_unless_explicitly_selected(tmp_path: Path) -> None:
    """The ordinary uninstall plan cannot include user-owned runtime data."""
    module = _load_script()

    default_targets = module.uninstall_targets(tmp_path, remove_data=False)

    assert default_targets == (
        tmp_path / "frontend" / "dist",
        tmp_path / "deploy" / "caddy-data",
        tmp_path / "deploy" / "caddy-config",
    )
    assert module.uninstall_targets(tmp_path, remove_data=True)[-1] == tmp_path / "data"


def test_apply_uninstall_removes_only_listed_generated_paths(tmp_path: Path) -> None:
    """Applying the default plan removes generated outputs while retaining data files."""
    module = _load_script()
    generated = tmp_path / "frontend" / "dist"
    generated.mkdir(parents=True)
    (generated / "index.html").write_text("generated", encoding="utf-8")
    data_file = tmp_path / "data" / "db" / "arch.sqlite3"
    data_file.parent.mkdir(parents=True)
    data_file.write_text("user state", encoding="utf-8")

    removed = module.apply_uninstall(
        tmp_path, module.uninstall_targets(tmp_path, remove_data=False)
    )

    assert removed == (generated,)
    assert not generated.exists()
    assert data_file.read_text(encoding="utf-8") == "user state"


def test_apply_uninstall_unlinks_a_generated_symlink_without_touching_its_target(
    tmp_path: Path,
) -> None:
    """A generated link is removed as a link instead of deleting its external target."""
    module = _load_script()
    external = tmp_path.parent / f"{tmp_path.name}-outside"
    external.mkdir()
    marker = external / "keep.txt"
    marker.write_text("outside data", encoding="utf-8")
    generated = tmp_path / "frontend" / "dist"
    generated.parent.mkdir(parents=True)
    generated.symlink_to(external, target_is_directory=True)

    assert module.apply_uninstall(tmp_path, (generated,)) == (generated,)
    assert not generated.exists()
    assert marker.read_text(encoding="utf-8") == "outside data"


def test_apply_uninstall_rejects_a_symlinked_parent_that_escapes_the_project(
    tmp_path: Path,
) -> None:
    """A redirected parent directory cannot make recursive removal leave the project root."""
    module = _load_script()
    external = tmp_path.parent / f"{tmp_path.name}-parent-escape"
    generated = external / "dist"
    generated.mkdir(parents=True)
    marker = generated / "keep.txt"
    marker.write_text("outside data", encoding="utf-8")
    frontend = tmp_path / "frontend"
    frontend.symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes the project root"):
        module.apply_uninstall(tmp_path, (frontend / "dist",))

    assert marker.read_text(encoding="utf-8") == "outside data"
