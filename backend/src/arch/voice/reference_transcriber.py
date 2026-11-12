"""Local, bounded transcription for editable voice-reference text drafts."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any, Protocol

from faster_whisper import WhisperModel  # type: ignore[import-untyped]

from arch.voice.models import VoiceModelPaths
from arch.voice.profiles import normalize_reference_audio


class WhisperModelLike(Protocol):
    """The small Faster-Whisper surface needed by the reference transcriber."""

    def transcribe(
        self,
        audio: str,
        *,
        language: str,
        beam_size: int,
        vad_filter: bool,
    ) -> tuple[Iterable[Any], Any]: ...


WhisperModelFactory = Callable[..., WhisperModelLike]


class ReferenceTranscriber(Protocol):
    """Application boundary for generating one editable reference transcript."""

    def transcribe(self, payload: bytes) -> str: ...

    def close(self) -> None: ...


class VoiceTranscriptionUnavailable(RuntimeError):
    """Raised when the local ASR model cannot be loaded or used."""


class VoiceReferenceTranscriber:
    """Keep one lazy local ASR model for short, authenticated reference drafts."""

    def __init__(
        self,
        paths: VoiceModelPaths,
        model_factory: WhisperModelFactory | None = None,
    ) -> None:
        self._paths = paths
        self._model_factory = model_factory or WhisperModel
        self._model: WhisperModelLike | None = None
        self._lock = Lock()

    def transcribe(self, payload: bytes) -> str:
        """Normalize one bounded audio payload and return its editable Chinese draft."""
        normalized = normalize_reference_audio(payload)
        with self._lock:
            model = self._get_model()
            with TemporaryDirectory(prefix="arch-reference-transcript-") as directory:
                audio_path = Path(directory) / "reference.wav"
                audio_path.write_bytes(normalized)
                try:
                    segments, _ = model.transcribe(
                        str(audio_path), language="zh", beam_size=1, vad_filter=True
                    )
                    transcript = "".join(str(segment.text) for segment in segments).strip()
                except Exception as error:
                    raise VoiceTranscriptionUnavailable(
                        "Local reference transcription failed."
                    ) from error
        if not transcript:
            raise ValueError("No speech was detected in the reference audio.")
        return transcript

    def close(self) -> None:
        """Release the cached model when the application shuts down."""
        with self._lock:
            self._model = None

    def _get_model(self) -> WhisperModelLike:
        if self._model is not None:
            return self._model
        if not self._paths.asr_model.is_dir():
            raise VoiceTranscriptionUnavailable("Local ASR model is not ready.")
        try:
            self._model = self._model_factory(
                str(self._paths.asr_model), device="cuda", compute_type="float16"
            )
        except Exception as error:
            raise VoiceTranscriptionUnavailable("Local ASR model is not available.") from error
        return self._model
