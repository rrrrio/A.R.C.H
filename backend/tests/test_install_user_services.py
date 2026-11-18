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


def test_rendered_backend_unit_uses_validated_resource_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    monkeypatch.setenv("ARCH_BACKEND_CPU_QUOTA", "600%")
    monkeypatch.setenv("ARCH_BACKEND_MEMORY_HIGH", "8G")
    monkeypatch.setenv("ARCH_BACKEND_MEMORY_MAX", "10G")
    monkeypatch.setenv("ARCH_BACKEND_TASKS_MAX", "256")

    rendered = module.render_units(tmp_path, tmp_path / "uv", tmp_path / "docker")

    backend = rendered["arch-backend.service"]
    assert "CPUQuota=600%" in backend
    assert "MemoryHigh=8G" in backend
    assert "MemoryMax=10G" in backend
    assert "TasksMax=256" in backend
    assert "check_service_readiness.py --target backend" in backend
    assert "check_service_readiness.py --target gateway" in rendered["arch-gateway.service"]


def test_rendered_avatar_media_unit_reads_the_local_environment_file() -> None:
    """The isolated runtime gets its shared ticket secret only from ignored `.env`."""
    module = _load_script()
    media = module.render_units(
        module.PROJECT_ROOT,
        module.PROJECT_ROOT / "uv",
        module.PROJECT_ROOT / "docker",
    )["arch-avatar-media.service"]

    assert "-m avatar_media_runtime" in media
    assert str(module.PROJECT_ROOT / "data/runtime/musetalk/bin/python") in media
    assert str(module.PROJECT_ROOT / "avatar_media_runtime/src") in media
    assert f"EnvironmentFile=-{module.PROJECT_ROOT / '.env'}" in media
    assert "After=arch-backend.service" in media
    assert "PartOf=arch-backend.service" in media
    assert "--host" not in media


def test_rendering_rejects_unsafe_resource_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    monkeypatch.setenv("ARCH_BACKEND_MEMORY_MAX", "12G\nExecStart=/bin/false")

    with pytest.raises(ValueError, match="ARCH_BACKEND_MEMORY_MAX"):
        module.render_units(tmp_path, tmp_path / "uv", tmp_path / "docker")


def test_rendering_rejects_a_memory_pressure_threshold_above_its_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    monkeypatch.setenv("ARCH_BACKEND_MEMORY_HIGH", "14G")
    monkeypatch.setenv("ARCH_BACKEND_MEMORY_MAX", "12G")

    with pytest.raises(ValueError, match="must not exceed"):
        module.render_units(tmp_path, tmp_path / "uv", tmp_path / "docker")


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


def test_install_units_can_write_only_the_selected_new_unit(tmp_path: Path) -> None:
    """A new avatar runtime unit must not require overwriting existing application units."""
    module = _load_script()
    destination = tmp_path / "units"
    destination.mkdir()
    (destination / "arch-backend.service").write_text("keep", encoding="utf-8")
    selected = {"arch-avatar-media.service": "[Unit]\nDescription=media\n"}

    written = module.install_units(destination, selected, replace=False)

    assert written == (destination / "arch-avatar-media.service",)
    assert (destination / "arch-backend.service").read_text(encoding="utf-8") == "keep"
