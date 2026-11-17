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
