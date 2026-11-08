"""Pipecat frame instrumentation for local per-turn latency telemetry."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from local_voice_agent.observability.metrics import LatencySample, LatencyStage, LatencyTracker

logger = logging.getLogger(__name__)

LatencyEvent = dict[str, str | int]


class VoiceLatencyProcessor(FrameProcessor):
    """Publish ordered server timing points without retaining or logging transcript text."""

    def __init__(
        self,
        send_event: Callable[[LatencyEvent], None],
        tracker: LatencyTracker,
        *,
        stages: frozenset[LatencyStage],
    ) -> None:
        super().__init__()
        self._send_event = send_event
        self._tracker = tracker
        self._stages = stages

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Emit bounded latency data before preserving the normal media flow."""
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            for sample in self._samples_for(frame):
                event = sample.as_event()
                self._send_event(event)
                logger.info("%s", json.dumps(event, separators=(",", ":"), sort_keys=True))
        await self.push_frame(frame, direction)

    def _samples_for(self, frame: Frame) -> tuple[LatencySample, ...]:
        if LatencyStage.SPEECH_START in self._stages and isinstance(
            frame, VADUserStartedSpeakingFrame
        ):
            return self._tracker.begin_speech()
        if LatencyStage.SPEECH_END in self._stages and isinstance(
            frame, VADUserStoppedSpeakingFrame
        ):
            return self._one(self._tracker.record(LatencyStage.SPEECH_END))
        if LatencyStage.ASR_FINAL in self._stages and isinstance(frame, TranscriptionFrame):
            return self._one(self._tracker.record(LatencyStage.ASR_FINAL))
        if LatencyStage.LLM_FIRST_TOKEN in self._stages and isinstance(frame, LLMTextFrame):
            return self._one(self._tracker.record(LatencyStage.LLM_FIRST_TOKEN))
        if LatencyStage.TTS_FIRST_BYTE in self._stages and isinstance(frame, TTSAudioRawFrame):
            return self._one(self._tracker.record(LatencyStage.TTS_FIRST_BYTE))
        if LatencyStage.PLAYBACK_FIRST_AUDIO in self._stages and isinstance(
            frame, OutputAudioRawFrame
        ):
            return self._one(self._tracker.record(LatencyStage.PLAYBACK_FIRST_AUDIO))
        if LatencyStage.PLAYBACK_STOPPED in self._stages and isinstance(frame, InterruptionFrame):
            return self._one(self._tracker.record(LatencyStage.PLAYBACK_STOPPED))
        return ()

    @staticmethod
    def _one(sample: LatencySample | None) -> tuple[LatencySample, ...]:
        return () if sample is None else (sample,)
