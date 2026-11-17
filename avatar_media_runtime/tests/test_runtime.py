"""Single-peer runtime session regression."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta

import av
import pytest
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError

from avatar_media_runtime.contracts import PreparedMedia
from avatar_media_runtime.media import SessionMedia
from avatar_media_runtime.runtime import AvatarMediaRuntime, MediaPreparationError, _log_timing
from avatar_media_runtime.tickets import MediaTicketSigner


def test_runtime_answer_contains_audio_and_video_on_one_peer() -> None:
    signer = MediaTicketSigner(b"test-secret-value")
    ticket = signer.verify_and_consume(
        signer.issue("browser", datetime.now(UTC) + timedelta(minutes=1)), "browser"
    )
    runtime = AvatarMediaRuntime()

    async def exercise() -> str:
        browser = RTCPeerConnection()
        browser.addTransceiver("audio", direction="recvonly")
        browser.addTransceiver("video", direction="recvonly")
        offer = await browser.createOffer()
        await browser.setLocalDescription(offer)
        for _ in range(50):
            if browser.iceGatheringState == "complete":
                break
            await asyncio.sleep(0.01)
        local = browser.localDescription
        assert local is not None and local.sdp is not None
        answer = await runtime.create_session(ticket, local.sdp)
        await browser.setRemoteDescription(RTCSessionDescription(sdp=answer.sdp, type="answer"))
        await runtime.close_session(answer.session_id)
        await browser.close()
        return answer.sdp

    sdp = asyncio.run(exercise())
    assert "m=audio" in sdp
    assert "m=video" in sdp


def test_submit_prepares_original_pcm_and_cancel_fences_the_utterance() -> None:
    class Backend:
        def __init__(self) -> None:
            self.prepared: tuple[bytes, str] | None = None
            self.cancelled: list[str] = []

        async def prepare(self, pcm: bytes, utterance_id: str) -> PreparedMedia:
            self.prepared = (pcm, utterance_id)
            return PreparedMedia(utterance_id, pcm, [av.VideoFrame(8, 8, "rgb24")], 25)

        async def cancel(self, utterance_id: str) -> None:
            self.cancelled.append(utterance_id)

    backend = Backend()
    runtime = AvatarMediaRuntime(backend)
    media = SessionMedia()
    runtime._sessions["session"] = (RTCPeerConnection(), media)

    async def exercise() -> tuple[object, object]:
        await runtime.submit("session", "turn", b"\x01\x00" * 480)
        audio = await media.audio.recv()
        video = await media.video.recv()
        await runtime.cancel("session", "turn")
        await runtime.close_session("session")
        return audio, video

    audio, video = asyncio.run(exercise())
    scoped_id = runtime._backend_utterance_id("session", "turn")
    assert backend.prepared == (b"\x01\x00" * 480, scoped_id)
    assert backend.cancelled == [scoped_id]
    assert audio.pts == video.pts == 0


def test_recvonly_peer_receives_both_tracks_after_submit() -> None:
    class Backend:
        async def prepare(self, pcm: bytes, utterance_id: str) -> PreparedMedia:
            return PreparedMedia(
                utterance_id,
                pcm,
                [av.VideoFrame(8, 8, "rgb24"), av.VideoFrame(8, 8, "rgb24")],
                25,
            )

        async def cancel(self, utterance_id: str) -> None:
            return

    signer = MediaTicketSigner(b"test-secret-value")
    ticket = signer.verify_and_consume(
        signer.issue("browser", datetime.now(UTC) + timedelta(minutes=1)), "browser"
    )
    runtime = AvatarMediaRuntime(Backend())

    async def exercise() -> tuple[set[str], list[float]]:
        browser = RTCPeerConnection()
        received: set[str] = set()
        arrivals: list[float] = []
        frames: asyncio.Queue[object] = asyncio.Queue()

        @browser.on("track")
        def on_track(track: object) -> None:
            received.add(track.kind)

            async def receive() -> None:
                await frames.put((track.kind, await track.recv()))
                arrivals.append(time.monotonic())

            asyncio.create_task(receive())

        browser.addTransceiver("audio", direction="recvonly")
        browser.addTransceiver("video", direction="recvonly")
        offer = await browser.createOffer()
        await browser.setLocalDescription(offer)
        for _ in range(50):
            if browser.iceGatheringState == "complete":
                break
            await asyncio.sleep(0.01)
        local = browser.localDescription
        assert local is not None and local.sdp is not None
        answer = await runtime.create_session(ticket, local.sdp)
        await browser.setRemoteDescription(RTCSessionDescription(sdp=answer.sdp, type="answer"))
        for _ in range(100):
            if browser.connectionState == "connected":
                break
            await asyncio.sleep(0.01)
        assert browser.connectionState == "connected"
        for index in range(4):
                await runtime.submit(answer.session_id, f"turn-{index}", b"\x01\x00" * 4_800)
        first = await asyncio.wait_for(frames.get(), timeout=1)
        try:
            second = await asyncio.wait_for(frames.get(), timeout=1)
        except TimeoutError as error:
            stats = await browser.getStats()
            inbound = [
                f"{report.kind}:{report.packetsReceived}"
                for report in stats.values()
                if report.type == "inbound-rtp"
            ]
            raise AssertionError(f"only decoded {first[0]}; inbound={inbound!r}") from error
        await runtime.close_session(answer.session_id)
        await browser.close()
        assert {first[0], second[0]} == {"audio", "video"}
        return received, arrivals

    received, arrivals = asyncio.run(exercise())
    assert received == {"audio", "video"}
    assert max(arrivals) - min(arrivals) <= 0.1


def test_cancel_stops_both_session_tracks_and_fences_backend() -> None:
    class Backend:
        def __init__(self) -> None:
            self.cancelled: list[str] = []

        async def prepare(self, pcm: bytes, utterance_id: str) -> PreparedMedia:
            return PreparedMedia(utterance_id, pcm, [], 25)

        async def cancel(self, utterance_id: str) -> None:
            self.cancelled.append(utterance_id)

    backend = Backend()
    runtime = AvatarMediaRuntime(backend)
    media = SessionMedia()
    runtime._sessions["session"] = (RTCPeerConnection(), media)

    async def exercise() -> None:
        await runtime.cancel("session", "turn")
        with pytest.raises(MediaStreamError):
            await media.audio.recv()
        with pytest.raises(MediaStreamError):
            await media.video.recv()
        await runtime.close_session("session")

    asyncio.run(exercise())
    assert backend.cancelled == [runtime._backend_utterance_id("session", "turn")]


def test_cancel_after_first_packets_stops_playing_tracks() -> None:
    """An interruption after media starts cannot leave queued packets playable."""
    class Backend:
        def __init__(self) -> None:
            self.cancelled: list[str] = []

        async def prepare(self, pcm: bytes, utterance_id: str) -> PreparedMedia:
            return PreparedMedia(
                utterance_id,
                pcm,
                [av.VideoFrame(8, 8, "rgb24") for _ in range(50)],
                25,
            )

        async def cancel(self, utterance_id: str) -> None:
            self.cancelled.append(utterance_id)

    backend = Backend()
    runtime = AvatarMediaRuntime(backend)
    media = SessionMedia()
    runtime._sessions["session"] = (RTCPeerConnection(), media)

    async def exercise() -> None:
        await runtime.submit("session", "turn", b"\x01\x00" * 48_000)
        await media.audio.recv()
        await media.video.recv()
        await runtime.cancel("session", "turn")
        with pytest.raises(MediaStreamError):
            await media.audio.recv()
        with pytest.raises(MediaStreamError):
            await media.video.recv()
        await runtime.close_session("session")

    asyncio.run(exercise())
    assert backend.cancelled == [runtime._backend_utterance_id("session", "turn")]


def test_second_browser_session_cannot_displace_the_active_peer() -> None:
    """The runtime owns exactly one authenticated browser peer at a time."""
    signer = MediaTicketSigner(b"test-secret-value")
    first_ticket = signer.verify_and_consume(
        signer.issue("first", datetime.now(UTC) + timedelta(minutes=1)), "first"
    )
    second_ticket = signer.verify_and_consume(
        signer.issue("second", datetime.now(UTC) + timedelta(minutes=1)), "second"
    )
    runtime = AvatarMediaRuntime()

    async def offer_from(browser: RTCPeerConnection) -> str:
        browser.addTransceiver("audio", direction="recvonly")
        browser.addTransceiver("video", direction="recvonly")
        offer = await browser.createOffer()
        await browser.setLocalDescription(offer)
        for _ in range(50):
            if browser.iceGatheringState == "complete":
                break
            await asyncio.sleep(0.01)
        local = browser.localDescription
        assert local is not None and local.sdp is not None
        return local.sdp

    async def exercise() -> None:
        first_browser = RTCPeerConnection()
        second_browser = RTCPeerConnection()
        try:
            first = await runtime.create_session(first_ticket, await offer_from(first_browser))
            with pytest.raises(RuntimeError, match="already exists"):
                await runtime.create_session(second_ticket, await offer_from(second_browser))
            assert set(runtime._sessions) == {"first"}
            await runtime.close_session(first.session_id)
        finally:
            await first_browser.close()
            await second_browser.close()

    asyncio.run(exercise())


def test_cancel_during_preparation_never_publishes_late_media() -> None:
    class Backend:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.cancelled: list[str] = []

        async def prepare(self, pcm: bytes, utterance_id: str) -> PreparedMedia:
            self.started.set()
            await self.release.wait()
            return PreparedMedia(utterance_id, pcm, [av.VideoFrame(8, 8, "rgb24")], 25)

        async def cancel(self, utterance_id: str) -> None:
            self.cancelled.append(utterance_id)

    backend = Backend()
    runtime = AvatarMediaRuntime(backend)
    media = SessionMedia()
    peer = RTCPeerConnection()
    runtime._sessions["session"] = (peer, media)

    async def exercise() -> None:
        submission = asyncio.create_task(runtime.submit("session", "turn", b"\x01\x00" * 480))
        await asyncio.wait_for(backend.started.wait(), timeout=1)
        await runtime.cancel("session", "turn")
        backend.release.set()
        with pytest.raises(MediaPreparationError):
            await submission
        with pytest.raises(MediaStreamError):
            await media.audio.recv()
        with pytest.raises(MediaStreamError):
            await media.video.recv()
        await runtime.close_session("session")

    asyncio.run(exercise())
    assert backend.cancelled == [runtime._backend_utterance_id("session", "turn")]


def test_cancelled_backend_identifier_cannot_reject_same_utterance_in_a_new_session() -> None:
    class Backend:
        def __init__(self) -> None:
            self.cancelled: set[str] = set()

        async def prepare(self, pcm: bytes, utterance_id: str) -> PreparedMedia:
            if utterance_id in self.cancelled:
                raise RuntimeError("stale cancellation")
            return PreparedMedia(utterance_id, pcm, [av.VideoFrame(8, 8, "rgb24")], 25)

        async def cancel(self, utterance_id: str) -> None:
            self.cancelled.add(utterance_id)

    backend = Backend()
    runtime = AvatarMediaRuntime(backend)
    runtime._sessions["first"] = (RTCPeerConnection(), SessionMedia())
    runtime._sessions["second"] = (RTCPeerConnection(), SessionMedia())

    async def exercise() -> None:
        await runtime.cancel("first", "tts:reused")
        await runtime.submit("second", "tts:reused", b"\x01\x00" * 480)
        await runtime.close_session("first")
        await runtime.close_session("second")

    asyncio.run(exercise())


def test_submit_surfaces_a_structured_renderer_failure() -> None:
    class Backend:
        async def prepare(self, pcm: bytes, utterance_id: str) -> PreparedMedia:
            raise RuntimeError("synthetic GPU failure")

        async def cancel(self, utterance_id: str) -> None:
            return

    runtime = AvatarMediaRuntime(Backend())

    async def exercise() -> None:
        with pytest.raises(MediaPreparationError) as failure:
            await runtime.submit("session", "turn", b"\x01\x00")
        assert failure.value.utterance_id == "turn"

    asyncio.run(exercise())


def test_runtime_timing_logs_only_fixed_metadata(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    _log_timing("session", "turn", "runtime-submission")

    event = json.loads(caplog.records[-1].message)
    assert event == {
        "event": "avatar-media-timing",
        "monotonic_ns": event["monotonic_ns"],
        "session_id": "session",
        "stage": "runtime-submission",
        "utterance_id": "turn",
    }
