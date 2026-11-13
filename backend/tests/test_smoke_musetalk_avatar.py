"""Tests for the owned MuseTalk WebRTC smoke command."""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


def _load_script() -> ModuleType:
    """Load the standalone script without running its command-line entry point."""
    script_path = Path(__file__).parents[1] / "scripts" / "smoke_musetalk_avatar.py"
    specification = importlib.util.spec_from_file_location("smoke_musetalk_avatar", script_path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_worker_command_uses_all_configured_musetalk_paths() -> None:
    """The temporary worker must use the active source, model, cache, and source checkout."""
    module = _load_script()
    settings = SimpleNamespace(
        avatar_source_video=Path("/avatars/idle.mp4"),
        avatar_fps=25,
        avatar_model_root=Path("/models/musetalk"),
        avatar_cache_dir=Path("/cache/avatar"),
        avatar_musetalk_source=Path("/source/musetalk"),
    )

    command = module.build_worker_command(
        Path("/runtime/python"), Path("/runtime/musetalk-smoke.sock"), settings
    )

    assert command == [
        "/runtime/python",
        "-m",
        "avatar_runtime.worker",
        "--socket",
        "/runtime/musetalk-smoke.sock",
        "--source",
        "/avatars/idle.mp4",
        "--fps",
        "25",
        "--backend",
        "musetalk",
        "--model-root",
        "/models/musetalk",
        "--cache-dir",
        "/cache/avatar",
        "--musetalk-source",
        "/source/musetalk",
    ]
