"""Tests for the authenticated video-avatar bridge and passive TTS observer."""

import asyncio
import base64
import json
import socket
from pathlib import Path

import pytest
from aiortc import RTCPeerConnection
from fastapi.testclient import TestClient
from pipecat.frames.frames import (
    Frame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from arch.auth.credentials import provision_credentials
from arch.auth.service import AuthService
from arch.avatar.video import AvatarVideoSignalingService, AvatarVideoTrack, UnixSocketAvatarWorker
from arch.main import create_app
from arch.voice.pipecat_output import PipecatOutputMuxProcessor

PUBLIC_ORIGIN = "https://avatar.test:8443"


class RecordingAvatar:
    """Record accepted TTS callbacks without creating a GPU worker."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        self.calls.append(("begin", f"{utterance_id}:{sample_rate}:{emotion}"))

    async def push_audio(self, utterance_id: str, audio: bytes, timestamp_ns: int) -> None:
        self.calls.append(("audio", f"{utterance_id}:{len(audio)}:{timestamp_ns}"))

    async def push_event(self, utterance_id: str, event: str) -> None:
        self.calls.append(("event", f"{utterance_id}:{event}"))

    async def cancel(self, utterance_id: str) -> None:
        self.calls.append(("cancel", utterance_id))

    async def end(self, utterance_id: str) -> None:
        self.calls.append(("end", utterance_id))


class ActiveRecordingAvatar(RecordingAvatar):
    """Model an observer with an active browser video session."""

    def is_active(self) -> bool:
        return True


class FakeWorker:
    """Accept the worker protocol while exposing the track callback to the test."""

    def __init__(self) -> None:
        self.on_frame: object | None = None

    async def start(self, on_frame: object) -> None:
        self.on_frame = on_frame

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        return

    async def push_audio(self, utterance_id: str, audio: bytes, timestamp_ns: int) -> None:
        return

    async def push_event(self, utterance_id: str, event: str) -> None:
        return

    async def cancel(self, utterance_id: str) -> None:
        return

    async def end(self, utterance_id: str) -> None:
        return

    async def close(self) -> None:
        return


class RecordingWriter:
    """Capture IPC writes without opening a Unix socket."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return


class BrokenPipeWriter(RecordingWriter):
    """Model a peer that reset its socket before local cleanup."""

    def close(self) -> None:
        return

    async def wait_closed(self) -> None:
        raise BrokenPipeError


class InspectingOutput(PipecatOutputMuxProcessor):
    """Capture direct-audio frames while exercising the observer boundary."""

    def __init__(self, observer: RecordingAvatar) -> None:
        super().__init__(24000, avatar_observer=observer)
        self.forwarded: list[Frame] = []

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        assert direction == FrameDirection.DOWNSTREAM
        self.forwarded.append(frame)

    async def _start_interruption(self) -> None:
        return


def test_tts_observer_receives_only_accepted_audio() -> None:
    """A video observer sees the same active utterance while direct audio remains emitted."""
    observer = RecordingAvatar()
    processor = InspectingOutput(observer)
    frame = TTSAudioRawFrame(b"\x01\x00", 24000, 1, context_id="turn")
    frame.pts = 123

    async def exercise() -> None:
        await processor.process_frame(TTSStartedFrame(context_id="turn"), FrameDirection.DOWNSTREAM)
        await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    asyncio.run(exercise())

    assert observer.calls == [
        ("begin", "tts:turn:24000:neutral"),
        ("audio", "tts:turn:2:123"),
    ]
    assert len(processor.forwarded) == 3


def test_active_avatar_releases_buffered_audio_before_tts_context_stops() -> None:
    """PCM must not be deferred behind the downstream TTSStoppedFrame."""
    observer = ActiveRecordingAvatar()
    processor = InspectingOutput(observer)
    frame = TTSAudioRawFrame(b"\x01\x00", 24000, 1, context_id="turn")

    async def exercise() -> None:
        await processor.process_frame(TTSStartedFrame(context_id="turn"), FrameDirection.DOWNSTREAM)
        await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
        await processor.process_frame(TTSStoppedFrame(context_id="turn"), FrameDirection.DOWNSTREAM)

    asyncio.run(exercise())

    audio_index = next(
        index
        for index, item in enumerate(processor.forwarded)
        if isinstance(item, OutputAudioRawFrame)
    )
    stopped_index = next(
        index
        for index, item in enumerate(processor.forwarded)
        if isinstance(item, TTSStoppedFrame)
    )
    assert audio_index < stopped_index


def test_avatar_track_drops_old_frames_under_backpressure() -> None:
    """The bounded browser queue applies backpressure instead of skipping a burst."""
    track = AvatarVideoTrack(fps=25, queue_size=2)

    async def exercise() -> None:
        await track.publish(b"old")
        await track.publish(b"middle")
        pending = asyncio.create_task(track.publish(b"new"))
        await asyncio.sleep(0)
        assert not pending.done()
        assert await track._frames.get() == b"old"  # noqa: SLF001
        await pending

    asyncio.run(exercise())

    assert track._frames.qsize() == 2  # noqa: SLF001
    assert asyncio.run(track._frames.get()) == b"middle"  # noqa: SLF001
    assert asyncio.run(track._frames.get()) == b"new"  # noqa: SLF001


def test_avatar_track_paces_frames_at_its_configured_rate() -> None:
    """A rendered batch must be emitted as video time, not an immediate burst."""
    now = 100.0
    delays: list[float] = []

    def clock() -> float:
        return now

    async def sleep(delay: float) -> None:
        nonlocal now
        delays.append(delay)
        now += delay

    async def exercise() -> None:
        track = AvatarVideoTrack(fps=25, queue_size=2, clock=clock, sleep=sleep)
        await track._wait_for_next_frame()  # noqa: SLF001
        await track._wait_for_next_frame()  # noqa: SLF001
        await track._wait_for_next_frame()  # noqa: SLF001

    asyncio.run(exercise())

    assert delays == pytest.approx([0.04, 0.04])


def test_unix_worker_ignores_expected_eof_while_closing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An explicit local close must make reader EOF a normal terminal state."""

    async def exercise() -> None:
        reader = asyncio.StreamReader()
        reader.feed_eof()
        worker = UnixSocketAvatarWorker(tmp_path / "avatar.sock")
        worker._reader = reader  # noqa: SLF001
        worker._closing = True  # noqa: SLF001
        await worker._read_messages()  # noqa: SLF001

    caplog.set_level("ERROR", logger="arch.avatar.video")
    asyncio.run(exercise())

    assert "Avatar worker IPC reader stopped." not in caplog.text


def test_unix_worker_reports_unexpected_eof(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An EOF without the local close fence must remain observable as a worker failure."""

    async def exercise() -> None:
        reader = asyncio.StreamReader()
        reader.feed_eof()
        worker = UnixSocketAvatarWorker(tmp_path / "avatar.sock")
        worker._reader = reader  # noqa: SLF001
        await worker._read_messages()  # noqa: SLF001

    caplog.set_level("ERROR", logger="arch.avatar.video")
    asyncio.run(exercise())

    assert "Avatar worker IPC reader stopped." in caplog.text


def test_unix_worker_close_ignores_a_peer_reset(tmp_path: Path) -> None:
    """A prior renderer disconnect must not turn session cleanup into a 500."""

    async def exercise() -> None:
        worker = UnixSocketAvatarWorker(tmp_path / "avatar.sock")
        worker._writer = BrokenPipeWriter()  # noqa: SLF001
        await worker.close()

    asyncio.run(exercise())


def test_unix_worker_splits_pcm_before_the_socket_line_limit(tmp_path: Path) -> None:
    """Large TTS frames must not overflow asyncio's default 64 KiB line reader."""

    async def exercise() -> list[dict[str, object]]:
        worker = UnixSocketAvatarWorker(tmp_path / "avatar.sock")
        writer = RecordingWriter()
        worker._writer = writer  # noqa: SLF001
        await worker.begin("turn", 24000, "neutral")
        await worker.push_audio("turn", b"\x01\x00" * 48000, 1_000_000_000)
        return [json.loads(item) for item in writer.writes]

    messages = asyncio.run(exercise())
    audio_messages = [message for message in messages if message["type"] == "audio"]

    assert len(audio_messages) == 4
    assert all(
        len(json.dumps(message, separators=(",", ":")).encode()) < 64 * 1024
        for message in audio_messages
    )
    assert b"".join(base64.b64decode(message["pcm_b64"]) for message in audio_messages) == (
        b"\x01\x00" * 48000
    )
    assert [message["timestamp_ns"] for message in audio_messages] == [
        1_000_000_000,
        1_500_000_000,
        2_000_000_000,
        2_500_000_000,
    ]


def test_unix_worker_reads_a_large_renderer_frame(tmp_path: Path) -> None:
    """Renderer JPEG Base64 records must exceed neither side's IPC line limit."""

    async def exercise() -> bytes:
        socket_path = tmp_path / "avatar.sock"
        received: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readline()
            writer.write(b'{"type":"ready"}\n')
            frame = {"type": "frame", "jpeg_b64": base64.b64encode(b"x" * 50000).decode()}
            writer.write(
                (json.dumps(frame) + "\n").encode()
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handle, path=socket_path)
        worker = UnixSocketAvatarWorker(socket_path)
        try:
            await worker.start(lambda payload: _set_result(received, payload))
            return await asyncio.wait_for(received, timeout=1)
        finally:
            await worker.close()
            server.close()
            await server.wait_closed()

    assert asyncio.run(exercise()) == b"x" * 50000


async def _set_result(result: asyncio.Future[bytes], payload: bytes) -> None:
    if not result.done():
        result.set_result(payload)


def test_rebuilding_avatar_rejects_the_legacy_video_only_peer() -> None:
    """Rebuild mode cannot start the split JPEG worker or create a video-only peer."""
    worker = FakeWorker()
    service = AvatarVideoSignalingService(
        enabled=True,
        model="musetalk-1.5",
        source_video=Path("data/avatars/idle.mp4"),
        socket_path=Path("data/runtime/avatar/avatar.sock"),
        worker_factory=lambda: worker,
    )

    async def exercise() -> None:
        browser = RTCPeerConnection()
        browser.addTransceiver("video", direction="recvonly")
        offer = await browser.createOffer()
        await browser.setLocalDescription(offer)
        local = browser.localDescription
        assert local is not None and local.sdp is not None
        with pytest.raises(Exception, match="Video avatar is being rebuilt"):
            await service.offer("owner", local.sdp, local.type)
        await browser.close()

    asyncio.run(exercise())

    assert worker.on_frame is None
    assert service.status().enabled is False
    assert service.status().state == "rebuilding"


def test_avatar_status_is_authenticated_and_rebuilding(tmp_path: Path) -> None:
    """The browser sees a clear rebuild state and cannot start the retired peer."""
    credential_path = tmp_path / "admin.json"
    provision_credentials(credential_path, "correct-horse-battery-staple")
    avatar = AvatarVideoSignalingService(
        enabled=False,
        model="musetalk-1.5",
        source_video=tmp_path / "idle.mp4",
        socket_path=tmp_path / "avatar.sock",
    )
    app = create_app(
        AuthService(credential_path, PUBLIC_ORIGIN),
        avatar_video_service=avatar,
    )

    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        anonymous = client.get("/api/avatar/status")
        login = client.post(
            "/api/auth/login",
            headers={"Origin": PUBLIC_ORIGIN},
            json={"password": "correct-horse-battery-staple"},
        )
        status_response = client.get("/api/avatar/status")
        csrf = login.json()["csrf_token"]
        offer = client.post(
            "/api/avatar/offer",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf},
            json={"sdp": "v=0", "type": "offer"},
        )

    assert anonymous.status_code == 401
    assert login.status_code == 200
    assert status_response.json() == {
        "enabled": False,
        "state": "rebuilding",
        "model": "musetalk-1.5",
        "source_video": "idle.mp4",
        "detail": "Video avatar is being rebuilt. Ordinary voice remains available.",
    }
    assert offer.status_code == 503


def test_rebuilding_avatar_ignores_a_ready_legacy_worker_socket(tmp_path: Path) -> None:
    """A live JPEG socket cannot make the retired video feature available."""
    socket_path = tmp_path / "avatar.sock"
    service = AvatarVideoSignalingService(
        enabled=True,
        model="musetalk-1.5",
        source_video=tmp_path / "idle.mp4",
        socket_path=socket_path,
    )

    assert service.status().state == "rebuilding"

    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(socket_path))
        assert service.status().state == "rebuilding"
        assert (
            service.status().detail
            == "Video avatar is being rebuilt. Ordinary voice remains available."
        )


def test_runtime_backed_status_is_ready_without_a_legacy_worker_socket(tmp_path: Path) -> None:
    """The replacement runtime controls public availability, never the retired socket bridge."""
    service = AvatarVideoSignalingService(
        enabled=True,
        model="wav2lip",
        source_video=tmp_path / "idle.mp4",
        socket_path=tmp_path / "legacy.sock",
        runtime_configured=lambda: True,
    )

    status = service.status(runtime_ready=True)

    assert status.enabled
    assert status.state == "ready"
    assert status.detail is None


def test_runtime_backed_status_is_unavailable_when_the_runtime_is_stopped(tmp_path: Path) -> None:
    """A configured client alone cannot make the browser start a dead runtime."""
    service = AvatarVideoSignalingService(
        enabled=True,
        model="wav2lip",
        source_video=tmp_path / "idle.mp4",
        socket_path=tmp_path / "legacy.sock",
        runtime_configured=lambda: True,
    )

    status = service.status(runtime_ready=False)

    assert not status.enabled
    assert status.state == "unavailable"
    assert status.detail == "Avatar media runtime is unavailable."
