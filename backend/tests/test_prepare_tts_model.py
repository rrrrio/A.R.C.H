"""Tests for verified TTS provenance persistence."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from pytest import MonkeyPatch


def _load_script() -> ModuleType:
    path = Path(__file__).parents[1] / "spikes" / "prepare_tts_model.py"
    specification = importlib.util.spec_from_file_location("prepare_tts_model", path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_prepare_tts_model_writes_verified_provenance(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    module = _load_script()
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "tts": {
                    "model": "repo",
                    "revision": "rev",
                    "model_filename": "model",
                    "model_sha256": "hash",
                    "voice_filename": "voice",
                    "voice_url": "https://example.test/voice",
                    "voice_sha256": "voice-hash",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(module, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(module, "hf_hub_download", lambda **_: tmp_path / "model")
    monkeypatch.setattr(module, "sha256_file", lambda _: "hash")
    monkeypatch.setattr(module, "download_voice_pack", lambda *_: None)

    module.main()

    assert json.loads((tmp_path / "models/rev/lva-provenance.json").read_text()) == {
        "source_repo": "repo",
        "revision": "rev",
        "model_filename": "model",
        "model_sha256": "hash",
        "voice_filename": "voice",
        "voice_url": "https://example.test/voice",
        "voice_sha256": "voice-hash",
    }


def test_prepare_tts_model_never_writes_provenance_after_a_model_hash_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    module = _load_script()
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "tts": {
                    "model": "repo",
                    "revision": "rev",
                    "model_filename": "model",
                    "model_sha256": "expected",
                    "voice_filename": "voice",
                    "voice_url": "https://example.test/voice",
                    "voice_sha256": "voice-hash",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(module, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(module, "hf_hub_download", lambda **_: tmp_path / "model")
    monkeypatch.setattr(module, "sha256_file", lambda _: "wrong")

    with pytest.raises(SystemExit, match="digest mismatch"):
        module.main()

    assert not (tmp_path / "models/rev/lva-provenance.json").exists()
