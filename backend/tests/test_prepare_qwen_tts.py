"""Tests for explicit, verified Qwen TTS artifact preparation."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from pytest import MonkeyPatch


def _load_script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "prepare_qwen_tts.py"
    specification = importlib.util.spec_from_file_location("prepare_qwen_tts", path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _settings(tmp_path: Path) -> SimpleNamespace:
    data_dir = tmp_path / "data"
    return SimpleNamespace(
        data_dir=data_dir,
        ensure_runtime_directories=lambda: (data_dir / "models").mkdir(parents=True, exist_ok=True),
    )


def test_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The default command describes the download without making local artifacts."""
    module = _load_script()
    settings = _settings(tmp_path)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["prepare_qwen_tts.py"])

    module.main()

    assert "Dry run" in capsys.readouterr().out
    assert not settings.data_dir.exists()


def test_prepare_verifies_and_records_pinned_artifacts(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A successful explicit snapshot receives provenance only after both checksums match."""
    module = _load_script()
    settings = _settings(tmp_path)

    def download(**kwargs: object) -> str:
        destination = Path(str(kwargs["local_dir"]))
        for relative_path in module.ARTIFACT_HASHES:
            artifact = destination / relative_path
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"model")
        return str(destination)

    calls: list[list[str]] = []
    monkeypatch.setattr(module, "snapshot_download", download)
    monkeypatch.setattr(module, "sha256_file", lambda _: "expected")
    monkeypatch.setattr(module, "ARTIFACT_HASHES", {"model.safetensors": "expected"})
    monkeypatch.setattr(module.subprocess, "run", lambda command, check: calls.append(command))

    destination, environment = module.prepare(settings)

    assert destination == settings.data_dir / "models" / "qwen3-tts" / module.MODEL_REVISION
    assert environment == settings.data_dir / "runtime" / "qwen-tts"
    assert json.loads((destination / "arch-provenance.json").read_text()) == {
        "source_repo": module.MODEL_REPOSITORY,
        "revision": module.MODEL_REVISION,
        "license": "Apache-2.0",
        "qwen_tts_version": module.QWEN_TTS_VERSION,
        "artifacts": {"model.safetensors": "expected"},
    }
    assert calls[0][:2] == ["uv", "venv"]
    assert calls[1][:3] == ["uv", "pip", "install"]


def test_prepare_rejects_unverified_existing_destination(tmp_path: Path) -> None:
    """An interrupted or manually copied snapshot cannot be silently trusted or replaced."""
    module = _load_script()
    destination = module.model_root(_settings(tmp_path))
    destination.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="without verified provenance"):
        module.prepare(_settings(tmp_path))


def test_prepare_does_not_write_provenance_when_a_digest_fails(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A bad payload cannot leave a record that makes a later run trust it."""
    module = _load_script()
    settings = _settings(tmp_path)

    def download(**kwargs: object) -> str:
        destination = Path(str(kwargs["local_dir"]))
        artifact = destination / "model.safetensors"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"bad")
        return str(destination)

    monkeypatch.setattr(module, "snapshot_download", download)
    monkeypatch.setattr(module, "ARTIFACT_HASHES", {"model.safetensors": "expected"})
    monkeypatch.setattr(module, "sha256_file", lambda _: "wrong")

    with pytest.raises(RuntimeError, match="digest mismatch"):
        module.prepare(settings)

    assert not (module.model_root(settings) / "arch-provenance.json").exists()
