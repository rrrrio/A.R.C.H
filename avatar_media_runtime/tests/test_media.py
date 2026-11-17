"""Deterministic in-memory media tracks for the single-peer runtime."""

from __future__ import annotations

import asyncio

import pytest
from aiortc.mediastreams import MediaStreamError

from avatar_media_runtime.media import DeterministicMedia


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
