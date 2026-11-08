"""Tests for ignored, mode-restricted SearXNG secret provisioning."""

import importlib.util
import stat
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    """Load the standalone provisioning script without running its command-line entry point."""
    script_path = Path(__file__).parents[1] / "scripts" / "provision_searxng_secret.py"
    specification = importlib.util.spec_from_file_location("provision_searxng_secret", script_path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_provision_secret_creates_one_mode_restricted_environment_file(tmp_path: Path) -> None:
    """First provisioning creates a nonempty secret without exposing it through a return value."""
    module = _load_script()
    path = module.secret_path(tmp_path)

    assert module.provision_secret(path)
    content = path.read_text(encoding="utf-8")
    assert content.startswith("SEARXNG_SECRET=")
    assert len(content.removeprefix("SEARXNG_SECRET=").strip()) >= module.MINIMUM_SECRET_LENGTH
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_provision_secret_preserves_a_valid_existing_secret(tmp_path: Path) -> None:
    """Repeated provisioning validates permissions without rotating the deployment key."""
    module = _load_script()
    path = module.secret_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("SEARXNG_SECRET=" + "a" * 32 + "\n", encoding="utf-8")
    path.chmod(0o644)

    assert not module.provision_secret(path)
    assert path.read_text(encoding="utf-8") == "SEARXNG_SECRET=" + "a" * 32 + "\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_provision_secret_rejects_malformed_existing_data(tmp_path: Path) -> None:
    """A corrupted secret file fails closed instead of being silently replaced."""
    module = _load_script()
    path = module.secret_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("SEARXNG_SECRET=short\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid secret"):
        module.provision_secret(path)


def test_provision_secret_rejects_an_existing_symbolic_link(tmp_path: Path) -> None:
    """A symbolic link cannot redirect validation or permission changes outside runtime data."""
    module = _load_script()
    target = tmp_path / "target.env"
    target.write_text("SEARXNG_SECRET=" + "a" * 32 + "\n", encoding="utf-8")
    path = module.secret_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.symlink_to(target)

    with pytest.raises(OSError):
        module.provision_secret(path)


def test_provision_secret_rejects_an_existing_hard_link(tmp_path: Path) -> None:
    """A hard link cannot share the runtime secret with another local file."""
    module = _load_script()
    target = tmp_path / "target.env"
    target.write_text("SEARXNG_SECRET=" + "a" * 32 + "\n", encoding="utf-8")
    path = module.secret_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.hardlink_to(target)

    with pytest.raises(ValueError, match="unlinked regular file"):
        module.provision_secret(path)
