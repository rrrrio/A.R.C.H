"""Route Pipecat TTS frames through the application's cancellable output boundary."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from arch.avatar.adapter import NullAvatarAdapter
from arch.voice.cancellation import CancellationController
from arch.voice.output_mux import OutputMux


class PipecatDirectAudioOutput:
    """Convert accepted mux PCM into the ordinary downstream Pipecat audio frame."""

    def __init__(self, emit_audio: Callable[[OutputAudioRawFrame], Awaitable[None]]) -> None:
        self._emit_audio = emit_audio
        self._sample_rate: int | None = None

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        """Remember the active output format after the mux accepted a new turn."""
        self._sample_rate = sample_rate

    async def push_audio(self, utterance_id: str, audio: bytes, timestamp_ns: int) -> None:
        """Emit mono PCM only after ``OutputMux`` has checked utterance ownership."""
        if self._sample_rate is None:
            raise RuntimeError("Audio output received PCM before an utterance began.")
        frame = OutputAudioRawFrame(audio, self._sample_rate, 1)
        frame.pts = timestamp_ns
        await self._emit_audio(frame)

    async def push_event(self, utterance_id: str, event: str) -> None:
        """Keep the direct-audio adapter neutral until expressive events gain a browser contract."""

    async def cancel(self, utterance_id: str) -> None:
        """The transport owns no additional queue at this adapter boundary."""

    async def end(self, utterance_id: str) -> None:
        """Finish without emitting a duplicate transport control frame."""


class PipecatOutputMuxProcessor(FrameProcessor):
    """Replace raw TTS frames with output-mux-routed frames for one Pipecat pipeline."""

    def __init__(self, output_sample_rate: int) -> None:
        super().__init__()
        self._cancellation = CancellationController()
        self._direct_audio = PipecatDirectAudioOutput(self._emit_audio)
        self._mux = OutputMux(self._cancellation, self._direct_audio, NullAvatarAdapter())
        self._output_sample_rate = output_sample_rate
        self._context_utterances: dict[str, str] = {}
        self._active_utterance_id: str | None = None
        self._anonymous_turn = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Forward controls while routing PCM only through the active output owner."""
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, InterruptionFrame):
            await self._cancel_active_output()
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, TTSStartedFrame):
            started_utterance_id = self._utterance_for_started(frame)
            await self._mux.begin(started_utterance_id, self._output_sample_rate)
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, TTSAudioRawFrame):
            audio_utterance_id = self._utterance_for_audio(frame)
            if audio_utterance_id is not None:
                await self._mux.push_audio(audio_utterance_id, frame.audio, self._timestamp(frame))
            return
        if isinstance(frame, TTSStoppedFrame):
            stopped_utterance_id = self._utterance_for_stopped(frame)
            if stopped_utterance_id is None or not await self._mux.end(stopped_utterance_id):
                self._discard_stopped_context(frame.context_id, stopped_utterance_id)
                return
            self._discard_stopped_context(frame.context_id, stopped_utterance_id)
            await self.push_frame(frame, direction)
            return
        await self.push_frame(frame, direction)

    async def _emit_audio(self, frame: OutputAudioRawFrame) -> None:
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)

    async def _cancel_active_output(self) -> None:
        utterance_id = self._cancellation.active_utterance_id
        if utterance_id is not None:
            await self._mux.cancel(utterance_id)
        self._active_utterance_id = None

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
        if context_id is not None:
            self._context_utterances.pop(context_id, None)
        if utterance_id == self._active_utterance_id:
            self._active_utterance_id = None

    @staticmethod
    def _timestamp(frame: TTSAudioRawFrame) -> int:
        return frame.pts if frame.pts is not None else time.monotonic_ns()
