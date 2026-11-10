"""Tests for rendered, explicitly installed user service units."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    script_path = Path(__file__).parents[1] / "scripts" / "install_user_services.py"
    specification = importlib.util.spec_from_file_location("install_user_services", script_path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_rendered_units_use_the_checkout_and_uv_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    templates = tmp_path / "templates"
    templates.mkdir()
    for name in module.UNIT_NAMES:
        (templates / f"{name}.in").write_text(
            "WorkingDirectory=@WORKING_DIRECTORY@\nExecStart=@UV_BIN@\nPath=@EXECUTION_PATH@\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(module, "TEMPLATE_DIRECTORY", templates)
    monkeypatch.setenv("PATH", "/test/path")

    rendered = module.render_units(
        tmp_path / "checkout %i with spaces",
        tmp_path / "bin with spaces" / "uv",
        tmp_path / "docker",
    )

    assert all("@" not in content for content in rendered.values())
    assert "checkout\\x20%%i\\x20with\\x20spaces" in rendered["arch-backend.service"]
    assert "/test/path" in rendered["arch-gateway.service"]


def test_install_units_refuses_existing_files_without_replace(tmp_path: Path) -> None:
    module = _load_script()
    units = {name: f"[{name}]\n" for name in module.UNIT_NAMES}
    destination = tmp_path / "units"
    destination.mkdir()
    (destination / module.UNIT_NAMES[0]).write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exist"):
        module.install_units(destination, units, replace=False)

    written = module.install_units(destination, units, replace=True)
    assert written == tuple(destination / name for name in module.UNIT_NAMES)
