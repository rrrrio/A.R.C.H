"""Deterministic audio/video tracks sharing one in-memory media timeline."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from fractions import Fraction

import av
import numpy as np
from aiortc.mediastreams import AudioStreamTrack, MediaStreamError, VideoStreamTrack

_AUDIO_RATE = 24_000
_AUDIO_SAMPLES = 480
_VIDEO_FPS = 25
logger = logging.getLogger("uvicorn.error")


class _AudioTrack(AudioStreamTrack):
    def __init__(self, pcm: bytes) -> None:
        super().__init__()
        self._pcm = pcm
        self._offset = 0
        self._pts = 0

    async def recv(self) -> av.AudioFrame:
        if self.readyState != "live" or self._offset >= len(self._pcm):
            raise MediaStreamError
        data = self._pcm[self._offset : self._offset + _AUDIO_SAMPLES * 2]
        self._offset += len(data)
        frame = av.AudioFrame(format="s16", layout="mono", samples=len(data) // 2)
        frame.planes[0].update(data)
        frame.pts = self._pts
        frame.sample_rate = _AUDIO_RATE
        frame.time_base = Fraction(1, _AUDIO_RATE)
        self._pts += frame.samples
        return frame


class _VideoTrack(VideoStreamTrack):
    def __init__(self, frame_count: int, width: int, height: int) -> None:
        super().__init__()
        self._remaining = frame_count
        self._width = width
        self._height = height
        self._pts = 0

    async def recv(self) -> av.VideoFrame:
        if self.readyState != "live" or self._remaining == 0:
            raise MediaStreamError
        pixels = np.zeros((self._height, self._width, 3), dtype=np.uint8)
        pixels[:, :, 1] = 128
        frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
        frame.pts = self._pts
        frame.time_base = Fraction(1, _VIDEO_FPS)
        self._pts += 1
        self._remaining -= 1
        return frame


class DeterministicMedia:
    """Prepared test media with audio and video tracks sharing zero presentation time."""

    def __init__(self, pcm_24khz: bytes, *, frame_count: int, width: int, height: int) -> None:
        if len(pcm_24khz) % 2:
            raise ValueError("PCM must contain whole 16-bit samples.")
        self.audio = _AudioTrack(pcm_24khz)
        self.video = _VideoTrack(frame_count, width, height)

    def cancel(self) -> None:
        """End both tracks so no stale prepared media can be emitted."""
        self.audio.stop()
        self.video.stop()


class SessionMedia:
    """Long-lived tracks that accept completed prepared media after negotiation."""

    def __init__(self) -> None:
        self.audio = _QueuedAudioTrack()
        self.video = _QueuedVideoTrack()

    async def publish(
        self,
        session_id: str,
        utterance_id: str,
        pcm_24khz: bytes,
        frames: list[av.VideoFrame],
        fps: int,
    ) -> None:
        if len(pcm_24khz) % 2:
            raise ValueError("PCM must contain whole 16-bit samples.")
        if fps != _VIDEO_FPS:
            raise ValueError("Prepared video must use 25 FPS.")
        await self.audio.publish(session_id, utterance_id, pcm_24khz)
        await self.video.publish(session_id, utterance_id, frames)

    def cancel(self, session_id: str, utterance_id: str) -> None:
        """Discard one interrupted turn without renegotiating the peer tracks."""
        self.audio.cancel(session_id, utterance_id)
        self.video.cancel(session_id, utterance_id)

    def stop(self) -> None:
        """Permanently end both tracks when their owning peer is released."""
        self.audio.stop()
        self.video.stop()


class _QueuedAudioTrack(_AudioTrack):
    def __init__(self) -> None:
        AudioStreamTrack.__init__(self)
        self._queue: asyncio.Queue[tuple[str, str, bytes] | None] = asyncio.Queue()
        self._pts = 0
        self._reported_utterances: set[tuple[str, str]] = set()
        self._cancelled_utterances: set[tuple[str, str]] = set()
        self._started_at: float | None = None

    async def publish(self, session_id: str, utterance_id: str, pcm: bytes) -> None:
        for offset in range(0, len(pcm), _AUDIO_SAMPLES * 2):
            chunk = pcm[offset : offset + _AUDIO_SAMPLES * 2]
            await self._queue.put((session_id, utterance_id, chunk))

    async def recv(self) -> av.AudioFrame:
        if self.readyState != "live":
            raise MediaStreamError
        while self.readyState == "live":
            item = await self._queue.get()
            if item is None or self.readyState != "live":
                raise MediaStreamError
            session_id, utterance_id, data = item
            if (session_id, utterance_id) in self._cancelled_utterances:
                continue
            await self._pace(self._pts / _AUDIO_RATE)
            if (session_id, utterance_id) in self._cancelled_utterances:
                continue
            if (session_id, utterance_id) not in self._reported_utterances:
                self._reported_utterances.add((session_id, utterance_id))
                _log_timing(session_id, utterance_id, "first-audio-packet")
            frame = av.AudioFrame(format="s16", layout="mono", samples=len(data) // 2)
            frame.planes[0].update(data)
            frame.pts = self._pts
            frame.sample_rate = _AUDIO_RATE
            frame.time_base = Fraction(1, _AUDIO_RATE)
            self._pts += frame.samples
            return frame
        raise MediaStreamError

    def cancel(self, session_id: str, utterance_id: str) -> None:
        self._cancelled_utterances.add((session_id, utterance_id))

    def stop(self) -> None:
        super().stop()
        self._queue.put_nowait(None)

    async def _pace(self, offset_seconds: float) -> None:
        if self._started_at is None:
            self._started_at = time.monotonic()
            return
        remaining = self._started_at + offset_seconds - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)


class _QueuedVideoTrack(_VideoTrack):
    def __init__(self) -> None:
        VideoStreamTrack.__init__(self)
        self._queue: asyncio.Queue[tuple[str, str, av.VideoFrame] | None] = asyncio.Queue()
        self._pts = 0
        self._reported_utterances: set[tuple[str, str]] = set()
        self._cancelled_utterances: set[tuple[str, str]] = set()
        self._started_at: float | None = None

    async def publish(
        self, session_id: str, utterance_id: str, frames: list[av.VideoFrame]
    ) -> None:
        for frame in frames:
            await self._queue.put((session_id, utterance_id, frame))

    async def recv(self) -> av.VideoFrame:
        if self.readyState != "live":
            raise MediaStreamError
        while self.readyState == "live":
            item = await self._queue.get()
            if item is None or self.readyState != "live":
                raise MediaStreamError
            session_id, utterance_id, frame = item
            if (session_id, utterance_id) in self._cancelled_utterances:
                continue
            await self._pace(self._pts / _VIDEO_FPS)
            if (session_id, utterance_id) in self._cancelled_utterances:
                continue
            if (session_id, utterance_id) not in self._reported_utterances:
                self._reported_utterances.add((session_id, utterance_id))
                _log_timing(session_id, utterance_id, "first-video-packet")
            frame.pts = self._pts
            frame.time_base = Fraction(1, _VIDEO_FPS)
            self._pts += 1
            return frame
        raise MediaStreamError

    def cancel(self, session_id: str, utterance_id: str) -> None:
        self._cancelled_utterances.add((session_id, utterance_id))

    def stop(self) -> None:
        super().stop()
        self._queue.put_nowait(None)

    async def _pace(self, offset_seconds: float) -> None:
        if self._started_at is None:
            self._started_at = time.monotonic()
            return
        remaining = self._started_at + offset_seconds - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)


def _log_timing(session_id: str, utterance_id: str, stage: str) -> None:
    """Emit timing metadata without retaining PCM or frame payloads."""
    logger.info(
        "%s",
        json.dumps(
            {
                "event": "avatar-media-timing",
                "monotonic_ns": time.monotonic_ns(),
                "session_id": session_id,
                "stage": stage,
                "utterance_id": utterance_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    )
