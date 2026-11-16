"""Tests for the editable local voice-reference transcription draft."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from arch.voice.models import VoiceModelPaths
from arch.voice.reference_transcriber import VoiceReferenceTranscriber


@dataclass(frozen=True)
class FakeSegment:
    """Minimal Faster-Whisper segment shape used by the transcriber boundary."""

    text: str


class FakeWhisperModel:
    """Inspect the temporary input and return deterministic segment text."""

    def __init__(self, segments: list[FakeSegment]) -> None:
        self.segments = segments
        self.calls: list[dict[str, Any]] = []

    def transcribe(
        self,
        audio: str,
        *,
        language: str,
        beam_size: int,
        vad_filter: bool,
    ) -> tuple[list[FakeSegment], object]:
        with wave.open(audio, "rb") as reference:
            assert reference.getnchannels() == 1
            assert reference.getsampwidth() == 2
        self.calls.append(
            {"audio": audio, "language": language, "beam_size": beam_size, "vad_filter": vad_filter}
        )
        return self.segments, object()


def _wav() -> bytes:
    payload = BytesIO()
    with wave.open(payload, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 16000)
    return payload.getvalue()


def _m4a() -> bytes:
    """Build a seek-required M4A fixture with media data before its metadata."""
    with tempfile.TemporaryDirectory() as directory:
        output_path = Path(directory) / "reference.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=24000",
                "-t",
                "7.584",
                "-c:a",
                "aac",
                "-f",
                "ipod",
                "-y",
                str(output_path),
            ],
            check=True,
        )
        return output_path.read_bytes()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="FFmpeg is required for audio normalization"
)
def test_transcriber_returns_editable_text_and_reuses_model(tmp_path: Path) -> None:
    """The local model is loaded once and receives only a temporary normalized WAV."""
    paths = VoiceModelPaths.from_data_dir(tmp_path)
    paths.asr_model.mkdir(parents=True)
    model = FakeWhisperModel([FakeSegment(" 这是 "), FakeSegment("参考文本 ")])
    factory_calls: list[tuple[str, dict[str, Any]]] = []

    def factory(model_path: str, **kwargs: Any) -> FakeWhisperModel:
        factory_calls.append((model_path, kwargs))
        return model

    transcriber = VoiceReferenceTranscriber(paths, model_factory=factory)

    assert transcriber.transcribe(_m4a()) == "这是 参考文本"
    assert transcriber.transcribe(_wav()) == "这是 参考文本"

    assert len(factory_calls) == 1
    assert factory_calls[0][1] == {"device": "cuda", "compute_type": "float16"}
    assert len(model.calls) == 2
    assert not Path(model.calls[0]["audio"]).exists()


def test_transcriber_rejects_audio_without_speech(tmp_path: Path) -> None:
    """An empty ASR result tells the UI to keep the manual transcript path available."""
    paths = VoiceModelPaths.from_data_dir(tmp_path)
    paths.asr_model.mkdir(parents=True)
    transcriber = VoiceReferenceTranscriber(
        paths, model_factory=lambda *_args, **_kwargs: FakeWhisperModel([])
    )

    with pytest.raises(ValueError, match="No speech"):
        transcriber.transcribe(_wav())


def test_transcriber_reports_missing_local_model(tmp_path: Path) -> None:
    """The API can distinguish an unprepared ASR model from a bad audio file."""
    with pytest.raises(RuntimeError, match="ASR model is not ready"):
        VoiceReferenceTranscriber(VoiceModelPaths.from_data_dir(tmp_path)).transcribe(_wav())
