"""Route Pipecat TTS frames through the application's cancellable output boundary."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import cast

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageUrgentFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from arch.avatar.adapter import AvatarAdapter, NullAvatarAdapter
from arch.voice.cancellation import CancellationController
from arch.voice.emotion import DEFAULT_EMOTION, Emotion, EmotionFrame
from arch.voice.output_mux import OutputMux

logger = logging.getLogger(__name__)


class PipecatDirectAudioOutput:
    """Convert accepted mux PCM into the ordinary downstream Pipecat audio frame."""

    def __init__(self, emit_audio: Callable[[OutputAudioRawFrame], Awaitable[None]]) -> None:
        self._emit_audio = emit_audio
        self._sample_rate: int | None = None
        self._utterance_id: str | None = None

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        """Remember the active output format after the mux accepted a new turn."""
        self._sample_rate = sample_rate
        self._utterance_id = utterance_id

    async def push_audio(self, utterance_id: str, audio: bytes, timestamp_ns: int) -> None:
        """Emit mono PCM only after ``OutputMux`` has checked utterance ownership."""
        if self._sample_rate is None:
            raise RuntimeError("Audio output received PCM before an utterance began.")
        frame = OutputAudioRawFrame(audio, self._sample_rate, 1)
        frame.pts = timestamp_ns
        frame.metadata["utterance_id"] = utterance_id
        await self._emit_audio(frame)

    async def push_event(self, utterance_id: str, event: str) -> None:
        """Keep the direct-audio adapter neutral until expressive events gain a browser contract."""

    async def cancel(self, utterance_id: str) -> None:
        """The transport owns no additional queue at this adapter boundary."""

    async def end(self, utterance_id: str) -> None:
        """Finish without emitting a duplicate transport control frame."""


class PipecatOutputMuxProcessor(FrameProcessor):
    """Replace raw TTS frames with output-mux-routed frames for one Pipecat pipeline."""

    def __init__(
        self,
        output_sample_rate: int,
        avatar_observer: AvatarAdapter | None = None,
    ) -> None:
        super().__init__()
        self._cancellation = CancellationController()
        self._direct_audio = PipecatDirectAudioOutput(self._emit_audio)
        self._mux = OutputMux(self._cancellation, self._direct_audio, NullAvatarAdapter())
        self._avatar_observer = avatar_observer
        self._output_sample_rate = output_sample_rate
        self._context_utterances: dict[str, str] = {}
        self._cancelled_context_ids: set[str] = set()
        self._active_utterance_id: str | None = None
        self._anonymous_turn = 0
        self._pending_emotion: Emotion = DEFAULT_EMOTION
        self._avatar_audio: dict[str, list[tuple[bytes, int]]] = {}

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Forward controls while routing PCM only through the active output owner."""
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, InterruptionFrame):
            self._pending_emotion = DEFAULT_EMOTION
            await self._cancel_active_output()
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, EmotionFrame):
            self._pending_emotion = frame.emotion
            return
        if isinstance(frame, TTSStartedFrame):
            if self._is_cancelled_context(frame.context_id):
                return
            started_utterance_id = self._utterance_for_started(frame)
            previous_utterance_id = self._cancellation.active_utterance_id
            if previous_utterance_id is not None and previous_utterance_id != started_utterance_id:
                await self._observe_avatar("cancel", previous_utterance_id)
            await self._mux.begin(
                started_utterance_id, self._output_sample_rate, self._pending_emotion
            )
            await self._observe_avatar(
                "begin", started_utterance_id, self._output_sample_rate, self._pending_emotion
            )
            if self._avatar_sync_active():
                self._avatar_audio[started_utterance_id] = []
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    message={
                        "type": "audio_started",
                        "utterance_id": started_utterance_id,
                    }
                ),
                direction,
            )
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, TTSAudioRawFrame):
            if self._is_cancelled_context(frame.context_id):
                return
            audio_utterance_id = self._utterance_for_audio(frame)
            if audio_utterance_id is not None:
                timestamp = self._timestamp(frame)
                buffered = self._avatar_audio.get(audio_utterance_id)
                if buffered is not None:
                    buffered.append((frame.audio, timestamp))
                    await self._observe_avatar("audio", audio_utterance_id, frame.audio, timestamp)
                else:
                    accepted = await self._mux.push_audio(
                        audio_utterance_id, frame.audio, timestamp
                    )
                    if accepted:
                        await self._observe_avatar(
                            "audio", audio_utterance_id, frame.audio, timestamp
                        )
            return
        if isinstance(frame, TTSStoppedFrame):
            if self._is_cancelled_context(frame.context_id):
                self._discard_stopped_context(frame.context_id, None)
                return
            stopped_utterance_id = self._utterance_for_stopped(frame)
            if stopped_utterance_id is None:
                self._discard_stopped_context(frame.context_id, stopped_utterance_id)
                return
            buffered = self._avatar_audio.pop(stopped_utterance_id, None)
            if buffered is not None:
                avatar_owns_playback = await self._release_avatar_audio(
                    stopped_utterance_id, buffered
                )
                self._discard_stopped_context(frame.context_id, stopped_utterance_id)
                if not avatar_owns_playback:
                    await self.push_frame(frame, direction)
                    return
                await self.push_frame(frame, direction)
                return
            if not await self._mux.end(stopped_utterance_id):
                self._discard_stopped_context(frame.context_id, stopped_utterance_id)
                return
            await self._observe_avatar("end", stopped_utterance_id)
            self._discard_stopped_context(frame.context_id, stopped_utterance_id)
            await self.push_frame(frame, direction)
            return
        await self.push_frame(frame, direction)

    async def _emit_audio(self, frame: OutputAudioRawFrame) -> None:
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)

    async def _cancel_active_output(self) -> None:
        # TTS emits audio asynchronously. A context can still produce a late
        # start or PCM frame after Pipecat has sent the interruption frame.
        # Fence every context known at this point so none can reactivate output.
        self._cancelled_context_ids.update(self._context_utterances)
        utterance_id = self._cancellation.active_utterance_id
        if utterance_id is not None:
            await self._mux.cancel(utterance_id)
            await self._observe_avatar("cancel", utterance_id)
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    message={"type": "audio_cancelled", "utterance_id": utterance_id}
                ),
                FrameDirection.DOWNSTREAM,
            )
        self._avatar_audio.clear()
        self._active_utterance_id = None

    def _avatar_sync_active(self) -> bool:
        observer = self._avatar_observer
        active = getattr(observer, "is_active", None)
        return callable(active) and bool(active())

    async def _release_avatar_audio(
        self, utterance_id: str, audio: list[tuple[bytes, int]]
    ) -> bool:
        """Release buffered PCM before the downstream TTS context is closed."""
        _log_avatar_timing(utterance_id, "tts-complete")
        avatar_owns_playback = await self._observe_avatar("end", utterance_id)
        if not self._cancellation.accepts(utterance_id):
            return False
        if avatar_owns_playback:
            return True
        _log_avatar_timing(
            utterance_id,
            "direct-audio-fallback",
            fallback_cause="runtime-unavailable-or-submission-failed",
        )
        for payload, timestamp in audio:
            if not self._cancellation.accepts(utterance_id):
                return False
            await self._direct_audio.push_audio(utterance_id, payload, timestamp)
        await self._mux.end(utterance_id)
        return False

    def _is_cancelled_context(self, context_id: str | None) -> bool:
        return context_id is not None and context_id in self._cancelled_context_ids

    def _utterance_for_started(self, frame: TTSStartedFrame) -> str:
        if frame.context_id is not None:
            utterance_id = f"tts:{frame.context_id}"
            self._context_utterances[frame.context_id] = utterance_id
        else:
            self._anonymous_turn += 1
            utterance_id = f"tts:anonymous-{self._anonymous_turn}"
        self._active_utterance_id = utterance_id
        return utterance_id

    def _utterance_for_audio(self, frame: TTSAudioRawFrame) -> str | None:
        if frame.context_id is not None:
            return self._context_utterances.get(frame.context_id)
        return self._active_utterance_id

    def _utterance_for_stopped(self, frame: TTSStoppedFrame) -> str | None:
        if frame.context_id is not None:
            return self._context_utterances.get(frame.context_id)
        return self._active_utterance_id

    def _discard_stopped_context(self, context_id: str | None, utterance_id: str | None) -> None:
        if utterance_id is not None:
            self._avatar_audio.pop(utterance_id, None)
        if context_id is not None:
            self._context_utterances.pop(context_id, None)
        if utterance_id == self._active_utterance_id:
            self._active_utterance_id = None

    @staticmethod
    def _timestamp(frame: TTSAudioRawFrame) -> int:
        return frame.pts if frame.pts is not None else time.monotonic_ns()

    async def _observe_avatar(self, kind: str, utterance_id: str, *payload: object) -> bool:
        """Keep renderer failures off the direct-audio critical path."""
        observer = self._avatar_observer
        if observer is None:
            return False
        try:
            if kind == "begin":
                await observer.begin(
                    utterance_id,
                    cast(int, payload[0]),
                    cast(str, payload[1]),
                )
            elif kind == "audio":
                await observer.push_audio(
                    utterance_id,
                    cast(bytes, payload[0]),
                    cast(int, payload[1]),
                )
            elif kind == "cancel":
                await observer.cancel(utterance_id)
            elif kind == "end":
                return bool(await observer.end(utterance_id))
        except Exception:
            logger.exception("Avatar video observer failed for %s.", kind)
        return False


def _log_avatar_timing(
    utterance_id: str, stage: str, *, fallback_cause: str | None = None
) -> None:
    """Log fixed lifecycle metadata without retaining audio or model content."""
    event: dict[str, str | int] = {
        "event": "avatar-media-timing",
        "monotonic_ns": time.monotonic_ns(),
        "stage": stage,
        "utterance_id": utterance_id,
    }
    if fallback_cause is not None:
        event["fallback_cause"] = fallback_cause
    logger.info("%s", json.dumps(event, separators=(",", ":"), sort_keys=True))
