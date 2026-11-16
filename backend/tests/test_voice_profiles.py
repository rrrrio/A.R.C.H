"""Tests for bounded local reference-voice profile storage."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import wave
from io import BytesIO
from pathlib import Path

import pytest

from arch.voice.profiles import VoiceProfileStore


def _wav(seconds: int = 1, sample_rate: int = 8000, channels: int = 1) -> bytes:
    payload = BytesIO()
    with wave.open(payload, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * seconds * sample_rate * channels)
    return payload.getvalue()


def _encode(payload: bytes, output_format: str) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            "pipe:0",
            "-f",
            output_format,
            "pipe:1",
        ],
        input=payload,
        capture_output=True,
        check=True,
    )
    return result.stdout


def _encode_m4a(payload: bytes) -> bytes:
    """Create a seekable M4A fixture with the same container users upload."""
    with tempfile.TemporaryDirectory() as directory:
        input_path = Path(directory) / "input.wav"
        output_path = Path(directory) / "reference.m4a"
        input_path.write_bytes(payload)
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-i",
                str(input_path),
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


def _m4a_with_seek_required_layout() -> bytes:
    """Build a deterministic 24 kHz M4A whose metadata is stored after media data."""
    with tempfile.TemporaryDirectory() as directory:
        input_path = Path(directory) / "input.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=24000",
                "-t",
                "7.584",
                "-c:a",
                "pcm_s16le",
                "-y",
                str(input_path),
            ],
            check=True,
        )
        return _encode_m4a(input_path.read_bytes())


def test_profile_store_requires_consent_and_valid_bounded_wav(tmp_path: Path) -> None:
    """Profiles reject unconsented, malformed, and overlong reference inputs."""
    store = VoiceProfileStore(tmp_path)

    with pytest.raises(ValueError, match="permission"):
        store.create("我的声音", "这是参考文本", _wav(), ownership_confirmed=False)
    with pytest.raises(ValueError, match="supported audio"):
        store.create("我的声音", "这是参考文本", b"not audio", ownership_confirmed=True)
    with pytest.raises(ValueError, match="at most"):
        store.create("我的声音", "这是参考文本", _wav(seconds=11), ownership_confirmed=True)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="FFmpeg is required for audio normalization"
)
def test_profile_store_normalizes_multichannel_wav_to_mono_pcm(tmp_path: Path) -> None:
    """A stereo reference is accepted and persisted as a bounded mono PCM WAV."""
    store = VoiceProfileStore(tmp_path)

    profile = store.create(
        "立体声参考", "这是参考文本", _wav(channels=2), ownership_confirmed=True
    )

    reference = tmp_path / "voices" / "profiles" / profile.id / "reference.wav"
    with wave.open(str(reference), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 24000
        assert audio.getnframes() > 0
    assert profile.sample_rate == 24000


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="FFmpeg is required for audio normalization"
)
def test_profile_store_accepts_flac_reference_and_normalizes_it(tmp_path: Path) -> None:
    """A common non-WAV input is decoded and stored in the internal WAV format."""
    store = VoiceProfileStore(tmp_path)

    profile = store.create(
        "FLAC 参考", "这是参考文本", _encode(_wav(), "flac"), ownership_confirmed=True
    )

    reference = tmp_path / "voices" / "profiles" / profile.id / "reference.wav"
    with wave.open(str(reference), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 24000
    assert profile.duration_ms == 1000


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="FFmpeg is required for audio normalization"
)
def test_profile_store_accepts_m4a_reference_and_normalizes_it(tmp_path: Path) -> None:
    """Seek-required M4A input is decoded before the local reference is stored."""
    store = VoiceProfileStore(tmp_path)

    profile = store.create(
        "M4A 参考", "这是参考文本", _m4a_with_seek_required_layout(), ownership_confirmed=True
    )

    reference = tmp_path / "voices" / "profiles" / profile.id / "reference.wav"
    with wave.open(str(reference), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 24000
    assert 7500 <= profile.duration_ms <= 7700


def test_profile_store_keeps_audio_local_and_resets_selected_profile_on_delete(
    tmp_path: Path,
) -> None:
    """Listed metadata omits paths and deleting a selected profile restores the baseline."""
    store = VoiceProfileStore(tmp_path)
    profile = store.create("我的声音", "这是参考文本", _wav(), ownership_confirmed=True)

    assert store.list() == (profile,)
    assert store.select(profile.id) == profile.id
    assert store.selected_id() == profile.id

    store.delete(profile.id)

    assert store.list() == ()
    assert store.selected_id() is None
    assert not (tmp_path / "voices" / "profiles" / profile.id).exists()


def test_profile_store_rejects_symbolic_linked_storage(tmp_path: Path) -> None:
    """A local link cannot redirect a browser upload outside the bounded data directory."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "voices").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        VoiceProfileStore(tmp_path).list()


def test_profile_store_ignores_metadata_that_does_not_match_its_directory(tmp_path: Path) -> None:
    """Corrupted metadata cannot make one profile alias another local directory."""
    store = VoiceProfileStore(tmp_path)
    profile = store.create("我的声音", "这是参考文本", _wav(), ownership_confirmed=True)
    metadata = tmp_path / "voices" / "profiles" / profile.id / "profile.json"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["id"] = "00000000-0000-0000-0000-000000000000"
    metadata.write_text(json.dumps(payload), encoding="utf-8")

    assert store.get(profile.id) is None
    assert store.list() == ()
