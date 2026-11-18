"""Deterministic in-memory media tracks for the single-peer runtime."""

from __future__ import annotations

import asyncio
import time

import av
import pytest
from aiortc.mediastreams import MediaStreamError

from avatar_media_runtime.media import DeterministicMedia, SessionMedia


def test_audio_and_video_start_at_the_same_zero_timeline() -> None:
    media = DeterministicMedia(b"\x01\x00" * 960, frame_count=2, width=8, height=8)

    async def receive() -> tuple[object, object]:
        return await media.audio.recv(), await media.video.recv()

    audio, video = asyncio.run(receive())

    assert audio.pts == 0
    assert audio.sample_rate == 24000
    assert video.pts == 0
    assert video.time_base.denominator == 25


def test_cancel_ends_both_tracks() -> None:
    media = DeterministicMedia(b"\x00\x00" * 480, frame_count=1, width=8, height=8)
    media.cancel()

    async def receive() -> None:
        with pytest.raises(MediaStreamError):
            await media.audio.recv()
        with pytest.raises(MediaStreamError):
            await media.video.recv()

    asyncio.run(receive())


def test_session_media_paces_audio_and_video_after_their_shared_zero_start() -> None:
    media = SessionMedia()

    async def receive() -> tuple[float, float]:
        await media.publish(
            "session",
            "answer",
            b"\x00\x00" * 960,
            [
                av.VideoFrame(8, 8, "rgb24"),
                av.VideoFrame(8, 8, "rgb24"),
            ],
            25,
        )
        audio_start = time.monotonic()
        await media.audio.recv()
        await media.audio.recv()
        audio_gap = time.monotonic() - audio_start
        video_start = time.monotonic()
        await media.video.recv()
        await media.video.recv()
        video_gap = time.monotonic() - video_start
        media.stop()
        return audio_gap, video_gap

    audio_gap, video_gap = asyncio.run(receive())
    assert audio_gap >= 0.015
    assert video_gap >= 0.035


def test_session_media_reanchors_pacing_for_a_later_utterance() -> None:
    """Idle time between answers must not make later media burst through the peer."""
    media = SessionMedia()

    async def receive() -> tuple[float, float]:
        await media.publish(
            "session", "first", b"\x00\x00" * 480, [av.VideoFrame(8, 8, "rgb24")], 25
        )
        await media.audio.recv()
        await media.video.recv()
        await asyncio.sleep(0.06)
        await media.publish(
            "session",
            "next",
            b"\x00\x00" * 960,
            [av.VideoFrame(8, 8, "rgb24"), av.VideoFrame(8, 8, "rgb24")],
            25,
        )
        audio_start = time.monotonic()
        await media.audio.recv()
        await media.audio.recv()
        audio_gap = time.monotonic() - audio_start
        video_start = time.monotonic()
        await media.video.recv()
        await media.video.recv()
        video_gap = time.monotonic() - video_start
        media.stop()
        return audio_gap, video_gap

    audio_gap, video_gap = asyncio.run(receive())

    assert audio_gap >= 0.015
    assert video_gap >= 0.035


def test_session_media_returns_to_idle_after_a_cancelled_turn() -> None:
    """A cancelled speaking turn must not leave the browser holding its final mouth frame."""

    def idle_frame() -> av.VideoFrame:
        frame = av.VideoFrame(8, 8, "rgb24")
        frame.planes[0].update(bytes([1]) * frame.planes[0].buffer_size)
        return frame

    media = SessionMedia(idle_frame_provider=idle_frame)

    async def receive() -> tuple[object, object, object, object]:
        idle_before = await media.video.recv()
        silent_before = await media.audio.recv()
        speaking = av.VideoFrame(8, 8, "rgb24")
        speaking.planes[0].update(bytes([2]) * speaking.planes[0].buffer_size)
        await media.publish("session", "turn", b"\x02\x00" * 480, [speaking], 25)
        speaking_frame = await media.video.recv()
        await media.audio.recv()
        media.cancel("session", "turn")
        idle_after = await media.video.recv()
        media.stop()
        return idle_before, silent_before, speaking_frame, idle_after

    idle_before, silent_before, speaking_frame, idle_after = asyncio.run(receive())

    assert int(idle_before.to_ndarray(format="rgb24")[0, 0, 0]) == 1
    assert bytes(silent_before.planes[0])[:960] == b"\x00" * 960
    assert int(speaking_frame.to_ndarray(format="rgb24")[0, 0, 0]) == 2
    assert int(idle_after.to_ndarray(format="rgb24")[0, 0, 0]) == 1
    assert [idle_before.pts, speaking_frame.pts, idle_after.pts] == [0, 1, 2]


def test_session_media_reports_idle_only_after_both_tracks_return_from_cancel() -> None:
    idle_markers: list[int] = []
    media = SessionMedia(
        idle_frame_provider=lambda: av.VideoFrame(8, 8, "rgb24"),
        idle_marker_sender=idle_markers.append,
    )

    async def receive() -> None:
        await media.publish(
            "session", "turn", b"\x01\x00" * 480, [av.VideoFrame(8, 8, "rgb24")], 25
        )
        await media.audio.recv()
        await media.video.recv()
        media.cancel("session", "turn")
        await media.audio.recv()
        assert idle_markers == []
        idle_video = await media.video.recv()
        assert idle_markers == [idle_video.pts]
        media.stop()

    asyncio.run(receive())


def test_session_media_reports_completion_only_after_both_tracks_return_to_idle() -> None:
    markers: list[str] = []
    media = SessionMedia(
        idle_frame_provider=lambda: av.VideoFrame(8, 8, "rgb24"),
        speaking_marker_sender=lambda: markers.append("speaking"),
        completion_marker_sender=lambda: markers.append("complete"),
    )

    async def receive() -> None:
        await media.publish(
            "session", "turn", b"\x01\x00" * 480, [av.VideoFrame(8, 8, "rgb24")], 25
        )
        await media.audio.recv()
        await media.video.recv()
        assert markers == ["speaking"]
        await media.audio.recv()
        assert markers == ["speaking"]
        await media.video.recv()
        assert markers == ["speaking", "complete"]
        media.stop()

    asyncio.run(receive())


def test_session_media_stop_during_idle_pacing_emits_no_late_frame() -> None:
    """Closing a peer during an idle wait must stop both media tracks immediately."""

    media = SessionMedia(idle_frame_provider=lambda: av.VideoFrame(8, 8, "rgb24"))

    async def receive() -> None:
        await media.audio.recv()
        await media.video.recv()
        audio = asyncio.create_task(media.audio.recv())
        video = asyncio.create_task(media.video.recv())
        await asyncio.sleep(0)
        media.stop()
        with pytest.raises(MediaStreamError):
            await asyncio.wait_for(audio, timeout=0.1)
        with pytest.raises(MediaStreamError):
            await asyncio.wait_for(video, timeout=0.1)

    asyncio.run(receive())


def test_session_media_cancel_discards_the_interrupted_turn_and_keeps_tracks_live() -> None:
    media = SessionMedia()

    async def receive() -> None:
        await media.publish(
            "session", "interrupted", b"\x01\x00" * 480, [av.VideoFrame(8, 8, "rgb24")], 25
        )
        media.cancel("session", "interrupted")
        await media.publish(
            "session", "next", b"\x02\x00" * 480, [av.VideoFrame(8, 8, "rgb24")], 25
        )
        audio = await asyncio.wait_for(media.audio.recv(), timeout=0.1)
        video = await asyncio.wait_for(media.video.recv(), timeout=0.1)
        assert bytes(audio.planes[0])[:960] == b"\x02\x00" * 480
        assert video.pts == 0
        assert media.audio.readyState == media.video.readyState == "live"
        media.stop()

    asyncio.run(receive())
