"""Tests for the temporary real-renderer browser smoke launcher."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    """Load the owned smoke launcher without creating a worker or browser."""
    script_path = Path(__file__).parents[1] / "scripts" / "run_owned_browser_avatar_smoke.py"
    specification = importlib.util.spec_from_file_location(
        "run_owned_browser_avatar_smoke", script_path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_worker_command_uses_a_private_socket_and_pinned_renderer_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owned browser check must not reuse the persistent avatar worker socket."""
    module = _load_script()
    settings = type(
        "Settings",
        (),
        {
            "avatar_source_video": Path("/data/avatars/idle.mp4"),
            "avatar_fps": 25,
            "avatar_model_root": Path("/data/models/musetalk"),
            "avatar_cache_dir": Path("/data/runtime/cache"),
            "avatar_musetalk_source": Path("/data/runtime/musetalk/source"),
        },
    )()
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "PROJECT_ROOT", Path("/project"))

    command = module.build_worker_command(Path("/data/e2e/private-avatar.sock"))

    assert command == [
        "/project/data/runtime/musetalk/bin/python",
        "-m",
        "avatar_runtime.worker",
        "--socket",
        "/data/e2e/private-avatar.sock",
        "--source",
        "/data/avatars/idle.mp4",
        "--fps",
        "25",
        "--backend",
        "musetalk",
        "--model-root",
        "/data/models/musetalk",
        "--cache-dir",
        "/data/runtime/cache",
        "--musetalk-source",
        "/data/runtime/musetalk/source",
    ]


def test_caddyfile_serves_only_the_container_static_mount(tmp_path: Path) -> None:
    """The proxy must expose the frontend mount rather than a host filesystem path."""
    module = _load_script()
    caddyfile = tmp_path / "Caddyfile"

    module.write_caddyfile(caddyfile, backend_port=18000, https_port=19443)

    content = caddyfile.read_text(encoding="utf-8")
    assert "https://arch-avatar-e2e.local:19443" in content
    assert "reverse_proxy 127.0.0.1:18000" in content
    assert "root * /srv" in content
