"""Tests for fixed local media-runtime artifact discovery."""

from pathlib import Path

import pytest

from local_voice_agent.voice.models import VoiceModelPaths


def test_voice_model_paths_report_every_missing_required_artifact(tmp_path: Path) -> None:
    """A session preflight exposes all absent model artifacts in one deterministic error."""
    paths = VoiceModelPaths.from_data_dir(tmp_path)

    assert len(paths.missing()) == 5
    with pytest.raises(RuntimeError, match="Local voice models are not ready"):
        paths.require_ready()


def test_voice_model_paths_accept_regular_files_only(tmp_path: Path) -> None:
    """Directories at expected locations cannot accidentally satisfy model readiness."""
    paths = VoiceModelPaths.from_data_dir(tmp_path)
    paths.asr_model.mkdir(parents=True)
    for path in (paths.llm_model, paths.llama_server, paths.tts_model, paths.tts_voices):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    assert paths.missing() == ()
    paths.require_ready()
