"""Tests for isolated browser voice acceptance setup."""

import importlib.util
import sys
import wave
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    script_path = Path(__file__).parents[1] / "scripts" / "run_browser_voice_e2e.py"
    specification = importlib.util.spec_from_file_location("run_browser_voice_e2e", script_path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_create_run_layout_isolates_credentials_without_linking_shared_artifacts(
    tmp_path: Path,
) -> None:
    module = _load_script()

    layout = module.create_run_layout(tmp_path)

    assert layout.root.parent == tmp_path / "e2e"
    assert not layout.root.joinpath("models").exists()
    assert not layout.root.joinpath("runtime").exists()
    assert layout.root.joinpath("auth", "admin.json").is_file()
    credential_contents = layout.root.joinpath("auth", "admin.json").read_text(encoding="utf-8")
    assert layout.password not in credential_contents


def test_write_caddyfile_uses_the_isolated_origin_and_backend_port(tmp_path: Path) -> None:
    module = _load_script()
    destination = tmp_path / "Caddyfile"

    module.write_caddyfile(destination, backend_port=18002, https_port=19443)

    content = destination.read_text(encoding="utf-8")
    assert "https://local-voice-agent-e2e.local:19443" in content
    assert "bind 127.0.0.1" in content
    assert "reverse_proxy 127.0.0.1:18002" in content
    assert "root * /srv" in content


def test_create_run_layout_removes_partial_directory_when_provisioning_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_script()

    def fail_provisioning(*_args: object, **_kwargs: object) -> None:
        raise OSError("test credential provisioning failure")

    monkeypatch.setattr(module, "provision_credentials", fail_provisioning)

    with pytest.raises(OSError, match="test credential provisioning failure"):
        module.create_run_layout(tmp_path)

    assert list((tmp_path / "e2e").iterdir()) == []


def test_create_barge_in_fixture_repeats_the_fixed_pcm_segment(tmp_path: Path) -> None:
    module = _load_script()
    source = tmp_path / "source.wav"
    destination = tmp_path / "barge-in.wav"
    with wave.open(str(source), "wb") as output_file:
        output_file.setparams((1, 2, 16_000, 96_000, "NONE", "not compressed"))
        output_file.writeframes(b"\\x01\\x02" * 96_000)

    module.create_barge_in_fixture(source, destination)

    with wave.open(str(destination), "rb") as output_file:
        assert output_file.getparams()[:3] == (1, 2, 16_000)
        assert output_file.getnframes() == 112_000
