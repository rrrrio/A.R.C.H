"""Bounded, local-only reference voice profiles for optional cloning."""

from __future__ import annotations

import json
import os
import uuid
import wave
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

MAX_PROFILE_NAME_CHARS = 80
MAX_REFERENCE_TRANSCRIPT_CHARS = 1000
MAX_REFERENCE_BYTES = 5 * 1024 * 1024
MAX_REFERENCE_SECONDS = 10


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    """Metadata for one consented local WAV reference without a filesystem path."""

    id: str
    name: str
    transcript: str
    sample_rate: int
    duration_ms: int
    created_at: str


class VoiceProfileStore:
    """Own per-profile local reference WAV files and the explicit active selection."""

    def __init__(self, data_dir: Path) -> None:
        self._root = data_dir / "voices"
        self._profiles = self._root / "profiles"
        self._selection = self._root / "selection.json"

    def create(
        self, name: str, transcript: str, reference_wav: bytes, *, ownership_confirmed: bool
    ) -> VoiceProfile:
        """Validate and atomically persist an attested, short mono PCM WAV reference."""
        if not ownership_confirmed:
            raise ValueError("Confirm that you have permission to use this reference voice.")
        normalized_name = _text(name, MAX_PROFILE_NAME_CHARS, "Voice profile name")
        normalized_transcript = _text(
            transcript, MAX_REFERENCE_TRANSCRIPT_CHARS, "Reference transcript"
        )
        sample_rate, duration_ms = _wav_details(reference_wav)
        self._ensure_root()
        profile_id = str(uuid.uuid4())
        profile = VoiceProfile(
            profile_id,
            normalized_name,
            normalized_transcript,
            sample_rate,
            duration_ms,
            _now(),
        )
        staging = self._profiles / f".{profile_id}.staging"
        destination = self._profiles / profile_id
        staging.mkdir()
        try:
            (staging / "reference.wav").write_bytes(reference_wav)
            (staging / "profile.json").write_text(
                json.dumps(asdict(profile), ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(staging, destination)
        except BaseException:
            _remove_tree(staging)
            raise
        return profile

    def list(self) -> tuple[VoiceProfile, ...]:
        """Return only complete regular profile directories in deterministic order."""
        self._ensure_root()
        profiles = [
            profile
            for directory in self._profiles.iterdir()
            if directory.is_dir() and not directory.is_symlink()
            if (profile := self._read_profile(directory)) is not None
        ]
        return tuple(
            sorted(profiles, key=lambda profile: (profile.created_at, profile.id), reverse=True)
        )

    def selected_id(self) -> str | None:
        """Return the active profile only when its complete local metadata still exists."""
        self._ensure_root()
        try:
            payload = json.loads(self._selection.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        profile_id = payload.get("profile_id") if isinstance(payload, dict) else None
        if not isinstance(profile_id, str) or self.get(profile_id) is None:
            return None
        return profile_id

    def get(self, profile_id: str) -> VoiceProfile | None:
        """Find one profile without accepting its identifier as a path."""
        if not _is_uuid(profile_id):
            return None
        directory = self._profiles / profile_id
        if not directory.is_dir() or directory.is_symlink():
            return None
        return self._read_profile(directory)

    def select(self, profile_id: str | None) -> str | None:
        """Select a profile or restore the baseline Kokoro mode with ``None``."""
        self._ensure_root()
        if profile_id is not None and self.get(profile_id) is None:
            raise KeyError("Unknown voice profile.")
        staging = self._root / ".selection.staging"
        staging.write_text(
            json.dumps({"profile_id": profile_id}, separators=(",", ":")), encoding="utf-8"
        )
        os.replace(staging, self._selection)
        return profile_id

    def delete(self, profile_id: str) -> None:
        """Delete only a known profile directory, resetting selection when necessary."""
        if self.get(profile_id) is None:
            raise KeyError("Unknown voice profile.")
        was_selected = self.selected_id() == profile_id
        directory = self._profiles / profile_id
        _remove_tree(directory)
        if was_selected:
            self.select(None)

    def _ensure_root(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        if self._root.is_symlink():
            raise ValueError("Voice profile storage cannot be a symbolic link.")
        self._profiles.mkdir(exist_ok=True)
        if self._profiles.is_symlink():
            raise ValueError("Voice profile directory cannot be a symbolic link.")

    @staticmethod
    def _read_profile(directory: Path) -> VoiceProfile | None:
        reference = directory / "reference.wav"
        metadata = directory / "profile.json"
        if (
            not reference.is_file()
            or reference.is_symlink()
            or not metadata.is_file()
            or metadata.is_symlink()
        ):
            return None
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        try:
            profile = VoiceProfile(**payload)
        except TypeError:
            return None
        if not (
            profile.id == directory.name
            and _is_uuid(profile.id)
            and isinstance(profile.name, str)
            and isinstance(profile.transcript, str)
            and isinstance(profile.sample_rate, int)
            and isinstance(profile.duration_ms, int)
            and isinstance(profile.created_at, str)
        ):
            return None
        return profile


def _text(value: str, maximum: int, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} cannot be empty.")
    if len(normalized) > maximum:
        raise ValueError(f"{label} cannot exceed {maximum} characters.")
    return normalized


def _wav_details(payload: bytes) -> tuple[int, int]:
    if not payload or len(payload) > MAX_REFERENCE_BYTES:
        raise ValueError(f"Reference WAV must be between 1 byte and {MAX_REFERENCE_BYTES} bytes.")
    try:
        with wave.open(BytesIO(payload), "rb") as reference:
            channels = reference.getnchannels()
            sample_width = reference.getsampwidth()
            sample_rate = reference.getframerate()
            frames = reference.getnframes()
    except (wave.Error, EOFError) as error:
        raise ValueError("Reference audio must be a valid WAV file.") from error
    if channels != 1 or sample_width != 2 or not 8000 <= sample_rate <= 48000:
        raise ValueError("Reference WAV must be mono 16-bit PCM at 8-48 kHz.")
    duration_ms = frames * 1000 // sample_rate
    if not 1 <= duration_ms <= MAX_REFERENCE_SECONDS * 1000:
        raise ValueError(f"Reference WAV must be at most {MAX_REFERENCE_SECONDS} seconds.")
    return sample_rate, duration_ms


def _remove_tree(directory: Path) -> None:
    if not directory.exists():
        return
    if directory.is_symlink():
        raise ValueError("Voice profile directory cannot be a symbolic link.")
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            _remove_tree(child)
        else:
            child.unlink()
    directory.rmdir()


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _now() -> str:
    return datetime.now(UTC).isoformat()
