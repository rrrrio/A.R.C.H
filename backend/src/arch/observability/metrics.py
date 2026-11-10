"""Monotonic, per-turn latency measurements with no transcript payloads."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class LatencyStage(StrEnum):
    """Ordered points on the server-side voice path."""

    SPEECH_START = "speech-start"
    SPEECH_END = "speech-end"
    ASR_FINAL = "asr-final"
    LLM_FIRST_TOKEN = "llm-first-token"
    TTS_FIRST_BYTE = "tts-first-byte"
    PLAYBACK_FIRST_AUDIO = "playback-first-audio"
    PLAYBACK_STOPPED = "playback-stopped"


_STAGE_ORDER = {stage: index for index, stage in enumerate(LatencyStage)}


@dataclass(frozen=True, slots=True)
class LatencySample:
    """One elapsed monotonic duration for a single local speech turn."""

    utterance_id: str
    stage: LatencyStage
    elapsed_ms: int

    def as_event(self) -> dict[str, str | int]:
        """Return a browser-safe event that contains no user or model content."""
        return {
            "type": "latency",
            "utterance_id": self.utterance_id,
            "stage": self.stage,
            "elapsed_ms": self.elapsed_ms,
        }


class LatencyTracker:
    """Track one ordered speech turn using only a monotonic clock."""

    def __init__(self, clock_ns: Callable[[], int] = time.monotonic_ns) -> None:
        self._clock_ns = clock_ns
        self._turn_number = 0
        self._utterance_id: str | None = None
        self._started_ns: int | None = None
        self._last_stage: LatencyStage | None = None

    @property
    def utterance_id(self) -> str | None:
        """Return the active latency turn, if speech has started."""
        return self._utterance_id

    def begin_speech(self) -> tuple[LatencySample, ...]:
        """Start a new turn, closing a prior playing turn when it was interrupted."""
        now_ns = self._clock_ns()
        samples: list[LatencySample] = []
        if self._can_record(LatencyStage.PLAYBACK_STOPPED):
            stopped = self._record(LatencyStage.PLAYBACK_STOPPED, now_ns)
            if stopped is not None:
                samples.append(stopped)
        self._turn_number += 1
        self._utterance_id = f"turn-{self._turn_number}"
        self._started_ns = now_ns
        self._last_stage = LatencyStage.SPEECH_START
        samples.append(LatencySample(self._utterance_id, LatencyStage.SPEECH_START, 0))
        return tuple(samples)

    def record(self, stage: LatencyStage) -> LatencySample | None:
        """Record an ordered stage once, rejecting duplicates and stale callbacks."""
        return self._record(stage, self._clock_ns())

    def _can_record(self, stage: LatencyStage) -> bool:
        return (
            self._utterance_id is not None
            and self._started_ns is not None
            and self._last_stage is not None
            and _STAGE_ORDER[stage] == _STAGE_ORDER[self._last_stage] + 1
        )

    def _record(self, stage: LatencyStage, now_ns: int) -> LatencySample | None:
        if not self._can_record(stage):
            return None
        assert self._utterance_id is not None
        assert self._started_ns is not None
        self._last_stage = stage
        return LatencySample(
            self._utterance_id,
            stage,
            max(0, (now_ns - self._started_ns) // 1_000_000),
        )
