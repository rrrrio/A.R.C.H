"""Deterministic audio/video tracks sharing one in-memory media timeline."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
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

    def __init__(
        self,
        idle_frame_provider: Callable[[], av.VideoFrame] | None = None,
        idle_marker_sender: Callable[[int], None] | None = None,
        speaking_marker_sender: Callable[[], None] | None = None,
        completion_marker_sender: Callable[[], None] | None = None,
    ) -> None:
        self._idle_marker_sender = idle_marker_sender
        self._speaking_marker_sender = speaking_marker_sender
        self._completion_marker_sender = completion_marker_sender
        self._pending_idle: dict[tuple[str, str], dict[str, int]] = {}
        self._pending_speaking: dict[tuple[str, str], set[str]] = {}
        self._pending_completion: dict[tuple[str, str], set[str]] = {}
        self.audio = _QueuedAudioTrack(
            emit_silence=idle_frame_provider is not None,
            on_idle_emitted=self._on_audio_idle,
            on_speaking_emitted=self._on_audio_speaking,
            on_speaking_completed=self._on_audio_completed,
        )
        self.video = _QueuedVideoTrack(
            idle_frame_provider,
            on_idle_emitted=self._on_video_idle,
            on_speaking_emitted=self._on_video_speaking,
            on_speaking_completed=self._on_video_completed,
        )

    def set_recorder_marker_senders(
        self,
        idle_sender: Callable[[int], None] | None,
        speaking_sender: Callable[[], None] | None,
        completion_sender: Callable[[], None] | None,
    ) -> None:
        """Attach the optional recorder-only peer callback after DataChannel negotiation."""
        self._idle_marker_sender = idle_sender
        self._speaking_marker_sender = speaking_sender
        self._completion_marker_sender = completion_sender

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
        self._pending_speaking.setdefault((session_id, utterance_id), set())
        await self.audio.publish(session_id, utterance_id, pcm_24khz)
        await self.video.publish(session_id, utterance_id, frames)

    def cancel(self, session_id: str, utterance_id: str) -> None:
        """Discard one interrupted turn without renegotiating the peer tracks."""
        self._pending_idle.setdefault((session_id, utterance_id), {})
        self._pending_speaking.pop((session_id, utterance_id), None)
        self._pending_completion.pop((session_id, utterance_id), None)
        self.audio.cancel(session_id, utterance_id)
        self.video.cancel(session_id, utterance_id)

    def stop(self) -> None:
        """Permanently end both tracks when their owning peer is released."""
        self.audio.stop()
        self.video.stop()

    def _on_audio_idle(self, session_id: str, utterance_id: str, pts: int) -> None:
        self._on_idle("audio", session_id, utterance_id, pts)

    def _on_video_idle(self, session_id: str, utterance_id: str, pts: int) -> None:
        self._on_idle("video", session_id, utterance_id, pts)

    def _on_audio_speaking(self, session_id: str, utterance_id: str) -> None:
        self._on_speaking("audio", session_id, utterance_id)

    def _on_video_speaking(self, session_id: str, utterance_id: str) -> None:
        self._on_speaking("video", session_id, utterance_id)

    def _on_audio_completed(self, session_id: str, utterance_id: str) -> None:
        self._on_completion("audio", session_id, utterance_id)

    def _on_video_completed(self, session_id: str, utterance_id: str) -> None:
        self._on_completion("video", session_id, utterance_id)

    def _on_idle(self, track: str, session_id: str, utterance_id: str, pts: int) -> None:
        state = self._pending_idle.get((session_id, utterance_id))
        if state is None:
            return
        state[track] = pts
        if "audio" not in state or "video" not in state:
            return
        self._pending_idle.pop((session_id, utterance_id), None)
        if self._idle_marker_sender is not None:
            self._idle_marker_sender(state["video"])

    def _on_speaking(self, track: str, session_id: str, utterance_id: str) -> None:
        state = self._pending_speaking.get((session_id, utterance_id))
        if state is None:
            return
        state.add(track)
        if state != {"audio", "video"}:
            return
        self._pending_speaking.pop((session_id, utterance_id), None)
        if self._speaking_marker_sender is not None:
            self._speaking_marker_sender()

    def _on_completion(self, track: str, session_id: str, utterance_id: str) -> None:
        state = self._pending_completion.setdefault((session_id, utterance_id), set())
        state.add(track)
        if state != {"audio", "video"}:
            return
        self._pending_completion.pop((session_id, utterance_id), None)
        if self._completion_marker_sender is not None:
            self._completion_marker_sender()


class _QueuedAudioTrack(_AudioTrack):
    def __init__(
        self,
        *,
        emit_silence: bool = False,
        on_idle_emitted: Callable[[str, str, int], None] | None = None,
        on_speaking_emitted: Callable[[str, str], None] | None = None,
        on_speaking_completed: Callable[[str, str], None] | None = None,
    ) -> None:
        AudioStreamTrack.__init__(self)
        self._queue: asyncio.Queue[tuple[str, str, bytes] | None] = asyncio.Queue()
        self._emit_silence = emit_silence
        self._pts = 0
        self._reported_utterances: set[tuple[str, str]] = set()
        self._cancelled_utterances: set[tuple[str, str]] = set()
        self._pacing_utterance: tuple[str, str] | None = None
        self._started_at: float | None = None
        self._turn_samples = 0
        self._idle_started_at: float | None = None
        self._idle_samples = 0
        self._on_idle_emitted = on_idle_emitted
        self._on_speaking_emitted = on_speaking_emitted
        self._on_speaking_completed = on_speaking_completed
        self._pending_idle_acks: set[tuple[str, str]] = set()
        self._force_idle = False

    async def publish(self, session_id: str, utterance_id: str, pcm: bytes) -> None:
        for offset in range(0, len(pcm), _AUDIO_SAMPLES * 2):
            chunk = pcm[offset : offset + _AUDIO_SAMPLES * 2]
            await self._queue.put((session_id, utterance_id, chunk))

    async def recv(self) -> av.AudioFrame:
        if self.readyState != "live":
            raise MediaStreamError
        while self.readyState == "live":
            if self._emit_silence and (self._force_idle or self._queue.empty()):
                return await self._recv_silence()
            item = await self._queue.get()
            if item is None or self.readyState != "live":
                raise MediaStreamError
            session_id, utterance_id, data = item
            if (session_id, utterance_id) in self._cancelled_utterances:
                continue
            self._idle_started_at = None
            self._begin_utterance(session_id, utterance_id)
            await self._pace(self._turn_samples / _AUDIO_RATE)
            if (session_id, utterance_id) in self._cancelled_utterances:
                continue
            if (session_id, utterance_id) not in self._reported_utterances:
                self._reported_utterances.add((session_id, utterance_id))
                _log_timing(session_id, utterance_id, "first-audio-packet")
                if self._on_speaking_emitted is not None:
                    self._on_speaking_emitted(session_id, utterance_id)
            frame = av.AudioFrame(format="s16", layout="mono", samples=len(data) // 2)
            frame.planes[0].update(data)
            frame.pts = self._pts
            frame.sample_rate = _AUDIO_RATE
            frame.time_base = Fraction(1, _AUDIO_RATE)
            self._pts += frame.samples
            self._turn_samples += frame.samples
            return frame
        raise MediaStreamError

    def cancel(self, session_id: str, utterance_id: str) -> None:
        self._cancelled_utterances.add((session_id, utterance_id))
        self._pending_idle_acks.add((session_id, utterance_id))
        self._force_idle = True

    def _begin_utterance(self, session_id: str, utterance_id: str) -> None:
        if self._pacing_utterance == (session_id, utterance_id):
            return
        self._pacing_utterance = (session_id, utterance_id)
        self._started_at = time.monotonic()
        self._turn_samples = 0

    async def _recv_silence(self) -> av.AudioFrame:
        if self._idle_started_at is None:
            self._idle_started_at = time.monotonic()
            self._idle_samples = 0
        await self._pace_idle(self._idle_samples / _AUDIO_RATE)
        if self.readyState != "live":
            raise MediaStreamError
        frame = av.AudioFrame(format="s16", layout="mono", samples=_AUDIO_SAMPLES)
        frame.planes[0].update(b"\x00" * _AUDIO_SAMPLES * 2)
        frame.pts = self._pts
        frame.sample_rate = _AUDIO_RATE
        frame.time_base = Fraction(1, _AUDIO_RATE)
        pending = tuple(self._pending_idle_acks)
        self._pending_idle_acks.clear()
        self._force_idle = False
        if self._on_idle_emitted is not None:
            for session_id, utterance_id in pending:
                self._on_idle_emitted(session_id, utterance_id, frame.pts)
        if (
            self._pacing_utterance is not None
            and self._pacing_utterance not in self._cancelled_utterances
        ):
            if self._on_speaking_completed is not None:
                self._on_speaking_completed(*self._pacing_utterance)
            self._pacing_utterance = None
        self._pts += frame.samples
        self._idle_samples += frame.samples
        return frame

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

    async def _pace_idle(self, offset_seconds: float) -> None:
        assert self._idle_started_at is not None
        remaining = self._idle_started_at + offset_seconds - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)


class _QueuedVideoTrack(_VideoTrack):
    def __init__(
        self,
        idle_frame_provider: Callable[[], av.VideoFrame] | None = None,
        on_idle_emitted: Callable[[str, str, int], None] | None = None,
        on_speaking_emitted: Callable[[str, str], None] | None = None,
        on_speaking_completed: Callable[[str, str], None] | None = None,
    ) -> None:
        VideoStreamTrack.__init__(self)
        self._queue: asyncio.Queue[tuple[str, str, av.VideoFrame] | None] = asyncio.Queue()
        self._idle_frame_provider = idle_frame_provider
        self._pts = 0
        self._reported_utterances: set[tuple[str, str]] = set()
        self._cancelled_utterances: set[tuple[str, str]] = set()
        self._pacing_utterance: tuple[str, str] | None = None
        self._started_at: float | None = None
        self._turn_frames = 0
        self._idle_started_at: float | None = None
        self._idle_frames = 0
        self._on_idle_emitted = on_idle_emitted
        self._on_speaking_emitted = on_speaking_emitted
        self._on_speaking_completed = on_speaking_completed
        self._pending_idle_acks: set[tuple[str, str]] = set()
        self._force_idle = False

    async def publish(
        self, session_id: str, utterance_id: str, frames: list[av.VideoFrame]
    ) -> None:
        for frame in frames:
            await self._queue.put((session_id, utterance_id, frame))

    async def recv(self) -> av.VideoFrame:
        if self.readyState != "live":
            raise MediaStreamError
        while self.readyState == "live":
            if self._idle_frame_provider is not None and (self._force_idle or self._queue.empty()):
                return await self._recv_idle()
            item = await self._queue.get()
            if item is None or self.readyState != "live":
                raise MediaStreamError
            session_id, utterance_id, frame = item
            if (session_id, utterance_id) in self._cancelled_utterances:
                continue
            self._idle_started_at = None
            self._begin_utterance(session_id, utterance_id)
            await self._pace(self._turn_frames / _VIDEO_FPS)
            if (session_id, utterance_id) in self._cancelled_utterances:
                continue
            if (session_id, utterance_id) not in self._reported_utterances:
                self._reported_utterances.add((session_id, utterance_id))
                _log_timing(session_id, utterance_id, "first-video-packet")
                if self._on_speaking_emitted is not None:
                    self._on_speaking_emitted(session_id, utterance_id)
            frame.pts = self._pts
            frame.time_base = Fraction(1, _VIDEO_FPS)
            self._pts += 1
            self._turn_frames += 1
            return frame
        raise MediaStreamError

    def cancel(self, session_id: str, utterance_id: str) -> None:
        self._cancelled_utterances.add((session_id, utterance_id))
        self._pending_idle_acks.add((session_id, utterance_id))
        self._force_idle = True

    def _begin_utterance(self, session_id: str, utterance_id: str) -> None:
        if self._pacing_utterance == (session_id, utterance_id):
            return
        self._pacing_utterance = (session_id, utterance_id)
        self._started_at = time.monotonic()
        self._turn_frames = 0

    async def _recv_idle(self) -> av.VideoFrame:
        if self._idle_started_at is None:
            self._idle_started_at = time.monotonic()
            self._idle_frames = 0
        await self._pace_idle(self._idle_frames / _VIDEO_FPS)
        if self.readyState != "live":
            raise MediaStreamError
        assert self._idle_frame_provider is not None
        frame = self._idle_frame_provider()
        frame.pts = self._pts
        frame.time_base = Fraction(1, _VIDEO_FPS)
        pending = tuple(self._pending_idle_acks)
        self._pending_idle_acks.clear()
        self._force_idle = False
        if self._on_idle_emitted is not None:
            for session_id, utterance_id in pending:
                self._on_idle_emitted(session_id, utterance_id, frame.pts)
        if (
            self._pacing_utterance is not None
            and self._pacing_utterance not in self._cancelled_utterances
        ):
            if self._on_speaking_completed is not None:
                self._on_speaking_completed(*self._pacing_utterance)
            self._pacing_utterance = None
        self._pts += 1
        self._idle_frames += 1
        return frame

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

    async def _pace_idle(self, offset_seconds: float) -> None:
        assert self._idle_started_at is not None
        remaining = self._idle_started_at + offset_seconds - time.monotonic()
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
