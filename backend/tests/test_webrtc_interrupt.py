"""Tests for discarding SmallWebRTC PCM buffered beyond Pipecat's output queue."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable

from pipecat.frames.frames import Frame, InterruptionFrame, TextFrame, VADUserStartedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection

from arch.voice.webrtc_interrupt import (
    BargeInInterruptionProcessor,
    WebRTCAudioInterruptionProcessor,
    clear_queued_webrtc_audio,
)


class _RecordingBargeInProcessor(BargeInInterruptionProcessor):
    """Capture VAD-derived cancellation frames without a running pipeline."""

    def __init__(self) -> None:
        super().__init__()
        self.forwarded: list[tuple[Frame, FrameDirection]] = []

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        self.forwarded.append((frame, direction))

    async def _start_interruption(self) -> None:
        """Avoid worker queue setup unrelated to direct frame routing."""


def test_barge_in_processor_emits_downstream_interruption_after_vad_start() -> None:
    """Every VAD speech start cancels active generation and playback downstream."""
    processor = _RecordingBargeInProcessor()

    asyncio.run(processor.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM))

    assert isinstance(processor.forwarded[0][0], VADUserStartedSpeakingFrame)
    assert isinstance(processor.forwarded[1][0], InterruptionFrame)
    assert all(direction == FrameDirection.DOWNSTREAM for _, direction in processor.forwarded)


class _Track:
    def __init__(self, queue: deque[tuple[bytes, asyncio.Future[bool] | None]]) -> None:
        self._chunk_queue = queue


class _Client:
    def __init__(self, track: _Track) -> None:
        self._audio_output_track = track


class _OutputTransport:
    def __init__(self, track: _Track) -> None:
        self._client = _Client(track)


class _RecordingProcessor(WebRTCAudioInterruptionProcessor):
    def __init__(self, clear_queued_audio: Callable[[], bool]) -> None:
        super().__init__(clear_queued_audio)
        self.forwarded: list[tuple[Frame, FrameDirection]] = []

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        self.forwarded.append((frame, direction))

    async def _start_interruption(self) -> None:
        """Avoid framework task setup outside this frame-routing unit test."""


def test_clear_queued_webrtc_audio_discards_pending_pcm_and_resolves_writer() -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        completion = loop.create_future()
        queue = deque([(b"old", None), (b"old", completion)])

        assert clear_queued_webrtc_audio(_OutputTransport(_Track(queue)))
        assert not queue
        assert completion.result() is False

    asyncio.run(scenario())


def test_interruption_processor_clears_only_downstream_interruptions() -> None:
    async def scenario() -> None:
        cleared = 0

        def clear() -> bool:
            nonlocal cleared
            cleared += 1
            return True

        processor = _RecordingProcessor(clear)
        normal = TextFrame("unchanged")
        await processor.process_frame(normal, FrameDirection.DOWNSTREAM)
        await processor.process_frame(InterruptionFrame(), FrameDirection.UPSTREAM)
        await processor.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)

        assert cleared == 1
        assert [direction for _, direction in processor.forwarded] == [
            FrameDirection.DOWNSTREAM,
            FrameDirection.UPSTREAM,
            FrameDirection.DOWNSTREAM,
        ]

    asyncio.run(scenario())
