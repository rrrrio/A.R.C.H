"""Tests for the attach-only browser MuseTalk smoke launcher."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    """Import the standalone launcher without opening Chrome."""
    script_path = Path(__file__).parents[1] / "scripts" / "run_browser_avatar_e2e.py"
    specification = importlib.util.spec_from_file_location("run_browser_avatar_e2e", script_path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_driver_configuration_uses_explicit_live_service_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The smoke must attach to the named service and retain its private Chrome profile."""
    module = _load_script()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_browser_avatar_e2e.py",
            "--url",
            "https://avatar.example:8443",
            "--password",
            "secret",
            "--audio",
            "/tmp/input.wav",
        ],
    )

    configuration = module.build_driver_configuration(
        module.parse_args(), Path("/tmp/avatar-profile"), 19222
    )

    assert configuration == {
        "url": "https://avatar.example:8443",
        "password": "secret",
        "chrome": "/usr/bin/google-chrome",
        "audio": "/tmp/input.wav",
        "timeoutSeconds": 240,
        "profileDir": "/tmp/avatar-profile",
        "debugPort": 19222,
    }


def test_driver_configuration_requires_a_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """An attach-only check cannot proceed anonymously against the protected API."""
    module = _load_script()
    monkeypatch.delenv("ARCH_AVATAR_BROWSER_PASSWORD", raising=False)
    monkeypatch.setattr(sys, "argv", ["run_browser_avatar_e2e.py"])

    with pytest.raises(ValueError, match="ARCH_AVATAR_BROWSER_PASSWORD"):
        module.build_driver_configuration(module.parse_args(), Path("/tmp/avatar-profile"), 19222)
