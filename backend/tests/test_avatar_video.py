"""Tests for the authenticated video-avatar bridge and passive TTS observer."""

import asyncio
from pathlib import Path

from aiortc import RTCPeerConnection
from fastapi.testclient import TestClient
from pipecat.frames.frames import Frame, TTSAudioRawFrame, TTSStartedFrame
from pipecat.processors.frame_processor import FrameDirection

from arch.auth.credentials import provision_credentials
from arch.auth.service import AuthService
from arch.avatar.video import AvatarVideoSignalingService, AvatarVideoTrack
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


def test_avatar_track_drops_old_frames_under_backpressure() -> None:
    """The bounded browser queue retains the newest renderer frame."""
    track = AvatarVideoTrack(fps=25, queue_size=2)

    async def exercise() -> None:
        await track.publish(b"old")
        await track.publish(b"middle")
        await track.publish(b"new")

    asyncio.run(exercise())

    assert track._frames.qsize() == 2  # noqa: SLF001
    assert asyncio.run(track._frames.get()) == b"middle"  # noqa: SLF001
    assert asyncio.run(track._frames.get()) == b"new"  # noqa: SLF001


def test_avatar_signaling_returns_a_video_only_answer() -> None:
    """The browser-facing peer has no audio section and is session-owned."""
    worker = FakeWorker()
    service = AvatarVideoSignalingService(
        enabled=True,
        model="musetalk-1.5",
        source_video=Path("data/avatars/idle.mp4"),
        socket_path=Path("data/runtime/avatar/avatar.sock"),
        worker_factory=lambda: worker,
    )

    async def exercise() -> dict[str, str]:
        browser = RTCPeerConnection()
        browser.addTransceiver("video", direction="recvonly")
        offer = await browser.createOffer()
        await browser.setLocalDescription(offer)
        local = browser.localDescription
        assert local is not None and local.sdp is not None
        answer = await service.offer("owner", local.sdp, local.type)
        await service.release("owner")
        await browser.close()
        return answer

    answer = asyncio.run(exercise())

    assert answer["type"] == "answer"
    assert "m=video" in answer["sdp"]
    assert "m=audio" not in answer["sdp"]


def test_avatar_status_is_authenticated_and_disabled_by_default(tmp_path: Path) -> None:
    """The browser cannot inspect or start the optional avatar anonymously."""
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
        "state": "disabled",
        "model": "musetalk-1.5",
        "source_video": "idle.mp4",
        "detail": None,
    }
    assert offer.status_code == 404
