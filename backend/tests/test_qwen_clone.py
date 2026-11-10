"""Tests for the profile-bound Qwen Pipecat TTS adapter."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from pathlib import Path

import pytest
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame

from arch.voice.profiles import VoiceProfile
from arch.voice.qwen_clone import (
    QwenCloneRuntime,
    QwenCloneTTSService,
    QwenCloneUnavailable,
    _read_message,
)


def _profile() -> VoiceProfile:
    return VoiceProfile(
        "00000000-0000-0000-0000-000000000000", "授权声音", "参考文本", 24000, 1000, "now"
    )


def test_qwen_clone_service_emits_profile_bound_24khz_pcm() -> None:
    """The adapter preserves Pipecat's ordinary PCM transport contract."""

    async def synthesize(text: str, profile: VoiceProfile) -> tuple[bytes, int]:
        assert text == "测试回答"
        assert profile == _profile()
        return b"\x01\x00", 24000

    async def exercise() -> list[object]:
        service = QwenCloneTTSService(_profile(), synthesize)
        return [frame async for frame in service.run_tts("测试回答", "turn-1")]

    frames = asyncio.run(exercise())

    assert len(frames) == 1
    assert isinstance(frames[0], TTSAudioRawFrame)
    assert frames[0].sample_rate == 24000
    assert frames[0].context_id == "turn-1"


def test_qwen_clone_service_does_not_hide_unavailable_or_wrong_format_runtime() -> None:
    """Bad clone output becomes an explicit error rather than a Kokoro fallback."""

    async def synthesize(_: str, __: VoiceProfile) -> tuple[bytes, int]:
        return b"\x01", 22050

    async def exercise() -> list[object]:
        service = QwenCloneTTSService(_profile(), synthesize)
        return [frame async for frame in service.run_tts("测试", "turn-2")]

    frames = asyncio.run(exercise())

    assert len(frames) == 1
    assert isinstance(frames[0], ErrorFrame)
    assert "unavailable" in frames[0].error


def test_runtime_requires_verified_model_and_isolated_python(tmp_path: Path) -> None:
    """Selecting a clone profile cannot start a session before explicit preparation."""
    runtime = QwenCloneRuntime(tmp_path)

    with pytest.raises(QwenCloneUnavailable, match="qwen-tts-prepare"):
        runtime.require_ready()


def test_runtime_reads_bounded_local_pcm_response() -> None:
    """The client accepts the worker's framed base64 PCM response without a file path."""

    async def exercise() -> dict[str, object]:
        reader = asyncio.StreamReader()
        payload = json.dumps(
            {"ok": True, "sample_rate": 24000, "audio": base64.b64encode(b"\x01\x00").decode()}
        ).encode()
        reader.feed_data(struct.pack("!I", len(payload)) + payload)
        reader.feed_eof()
        return await _read_message(reader)

    response = asyncio.run(exercise())

    assert response["sample_rate"] == 24000
