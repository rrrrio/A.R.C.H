"""Tests for the profile-bound Qwen Pipecat TTS adapter."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from pathlib import Path

import pytest
from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame, TTSStoppedFrame
from pipecat.processors.frame_processor import FrameDirection

from arch.voice import qwen_clone as qwen_clone_module
from arch.voice.profiles import VoiceProfile
from arch.voice.qwen_clone import (
    QwenCloneRuntime,
    QwenCloneTTSService,
    QwenCloneUnavailable,
    _read_message,
    split_clone_text,
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


def test_qwen_clone_service_keeps_context_open_until_slow_first_audio_arrives() -> None:
    """A slow first Qwen frame must not be discarded by Pipecat's default timeout."""

    async def synthesize(_: str, __: VoiceProfile) -> tuple[bytes, int]:
        await asyncio.sleep(3.25)
        return b"\x01\x00", 24000

    async def exercise() -> list[Frame]:
        service = QwenCloneTTSService(_profile(), synthesize)
        emitted: list[Frame] = []

        async def capture(
            frame: Frame, _: FrameDirection = FrameDirection.DOWNSTREAM
        ) -> None:
            emitted.append(frame)

        service.push_frame = capture  # type: ignore[method-assign]
        service.start_word_timestamps = lambda: asyncio.sleep(0)  # type: ignore[method-assign]
        service.process_ttfa_metrics = lambda _: asyncio.sleep(0)  # type: ignore[method-assign]
        service._audio_context_task = asyncio.create_task(service._audio_context_task_handler())
        await service.create_audio_context("slow-turn")
        service._turn_context_id = "slow-turn"
        await service._synthesize_text("慢速回答", "slow-turn")
        await service.append_to_audio_context(
            "slow-turn", TTSStoppedFrame(context_id="slow-turn")
        )
        await service.remove_audio_context("slow-turn")
        await asyncio.sleep(0.05)
        audio_context_task = service._audio_context_task
        if audio_context_task is not None:
            audio_context_task.cancel()
            await asyncio.gather(audio_context_task, return_exceptions=True)
        return emitted

    frames = asyncio.run(exercise())

    assert any(isinstance(frame, TTSAudioRawFrame) for frame in frames)
    assert not any(isinstance(frame, ErrorFrame) for frame in frames)


def test_qwen_clone_service_emits_each_completed_sentence_before_synthesizing_the_next() -> None:
    """A slow clone worker cannot hold later sentence PCM behind a full response."""
    received: list[str] = []

    async def synthesize(text: str, _: VoiceProfile) -> tuple[bytes, int]:
        received.append(text)
        return bytes([len(received), 0]), 24000

    async def exercise() -> list[object]:
        service = QwenCloneTTSService(_profile(), synthesize)
        return [frame async for frame in service.run_tts("第一句。第二句！尾句", "turn-1")]

    frames = asyncio.run(exercise())

    assert received == ["第一句。", "第二句！", "尾句"]
    assert [frame.audio for frame in frames if isinstance(frame, TTSAudioRawFrame)] == [
        b"\x01\x00",
        b"\x02\x00",
        b"\x03\x00",
    ]


def test_split_clone_text_bounds_punctuation_free_input() -> None:
    """A long model response without terminal punctuation still has finite worker requests."""
    assert split_clone_text("甲" * 5, maximum_chars=2) == ("甲甲", "甲甲", "甲")
    with pytest.raises(ValueError, match="positive"):
        split_clone_text("文本", maximum_chars=0)


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


def test_runtime_restarts_worker_before_synthesizing_after_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupted clone turn must not silence every later turn in the session."""

    class FakeProcess:
        returncode = None

    class FakeWriter:
        def __init__(self) -> None:
            self.payload = b""

        def write(self, payload: bytes) -> None:
            self.payload += payload

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    class FakeReader:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        async def readexactly(self, size: int) -> bytes:
            value, self.payload = self.payload[:size], self.payload[size:]
            return value

    starts = 0
    runtime = QwenCloneRuntime(tmp_path)
    monkeypatch.setattr(runtime, "_process", FakeProcess())

    async def stop() -> None:
        monkeypatch.setattr(runtime, "_process", None)

    async def start() -> None:
        nonlocal starts
        starts += 1
        monkeypatch.setattr(runtime, "_process", FakeProcess())

    monkeypatch.setattr(runtime, "stop", stop)
    monkeypatch.setattr(runtime, "start", start)
    payload = json.dumps(
        {"ok": True, "sample_rate": 24000, "audio": base64.b64encode(b"\x01\x00").decode()}
    ).encode()
    reader = FakeReader(struct.pack("!I", len(payload)) + payload)
    writer = FakeWriter()

    async def open_connection(*_: object, **__: object) -> tuple[FakeReader, FakeWriter]:
        return reader, writer

    monkeypatch.setattr(qwen_clone_module.asyncio, "open_unix_connection", open_connection)

    async def exercise() -> tuple[bytes, int]:
        await runtime.cancel()
        return await runtime.synthesize("下一句", _profile())

    assert asyncio.run(exercise()) == (b"\x01\x00", 24000)
    assert starts == 1


def test_runtime_restarts_worker_after_a_rejected_synthesis_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One transient worker failure must not poison later clone turns."""

    class FakeProcess:
        returncode = None

    class FakeWriter:
        def write(self, _: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    class FakeReader:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        async def readexactly(self, size: int) -> bytes:
            value, self.payload = self.payload[:size], self.payload[size:]
            return value

    def response(payload: dict[str, object]) -> FakeReader:
        encoded = json.dumps(payload).encode()
        return FakeReader(struct.pack("!I", len(encoded)) + encoded)

    starts = 0
    connections = 0
    runtime = QwenCloneRuntime(tmp_path)
    monkeypatch.setattr(runtime, "_process", FakeProcess())

    async def stop() -> None:
        monkeypatch.setattr(runtime, "_process", None)

    async def start() -> None:
        nonlocal starts
        starts += 1
        monkeypatch.setattr(runtime, "_process", FakeProcess())

    monkeypatch.setattr(runtime, "stop", stop)
    monkeypatch.setattr(runtime, "start", start)
    readers = iter(
        (
            response({"ok": False, "error": "RuntimeError"}),
            response(
                {"ok": True, "sample_rate": 24000, "audio": base64.b64encode(b"\x01\x00").decode()}
            ),
        )
    )

    async def open_connection(*_: object, **__: object) -> tuple[FakeReader, FakeWriter]:
        nonlocal connections
        connections += 1
        return next(readers), FakeWriter()

    monkeypatch.setattr(qwen_clone_module.asyncio, "open_unix_connection", open_connection)

    assert asyncio.run(runtime.synthesize("下一句", _profile())) == (b"\x01\x00", 24000)
    assert starts == 1
    assert connections == 2


def test_qwen_clone_service_cancels_its_runtime_when_pipecat_cancels_synthesis() -> None:
    """An interruption terminates the active isolated worker rather than only dropping PCM."""

    async def exercise() -> bool:
        started = asyncio.Event()
        cancelled = False

        async def synthesize(_: str, __: VoiceProfile) -> tuple[bytes, int]:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def cancel() -> None:
            nonlocal cancelled
            cancelled = True

        service = QwenCloneTTSService(_profile(), synthesize, cancel)

        async def consume() -> list[object]:
            return [frame async for frame in service.run_tts("测试", "turn-3")]

        task = asyncio.create_task(consume())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return cancelled

    assert asyncio.run(exercise())
