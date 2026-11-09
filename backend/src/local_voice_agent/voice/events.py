"""Translate public Pipecat conversation frames into browser-safe voice events."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypedDict, cast

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class VoiceEvent(TypedDict):
    """Small JSON message sent through the authenticated WebRTC data channel."""

    type: str
    state: str
    role: str
    phase: str
    text: str


def event_from_frame(frame: Frame) -> VoiceEvent | None:
    """Return the client event represented by a public Pipecat frame, if any."""
    if isinstance(frame, VADUserStartedSpeakingFrame):
        return {
            "type": "voice_state",
            "state": "user-speaking",
            "role": "",
            "phase": "",
            "text": "",
        }
    if isinstance(frame, VADUserStoppedSpeakingFrame):
        return {
            "type": "voice_state",
            "state": "recognizing",
            "role": "",
            "phase": "",
            "text": "",
        }
    if isinstance(frame, TranscriptionFrame):
        return {
            "type": "transcript",
            "state": "thinking",
            "role": "user",
            "phase": "final",
            "text": frame.text,
        }
    if isinstance(frame, LLMFullResponseStartFrame):
        return {
            "type": "transcript",
            "state": "thinking",
            "role": "assistant",
            "phase": "start",
            "text": "",
        }
    if isinstance(frame, LLMTextFrame):
        return {
            "type": "transcript",
            "state": "thinking",
            "role": "assistant",
            "phase": "delta",
            "text": frame.text,
        }
    if isinstance(frame, LLMFullResponseEndFrame):
        return {
            "type": "transcript",
            "state": "thinking",
            "role": "assistant",
            "phase": "end",
            "text": "",
        }
    if isinstance(frame, TTSStartedFrame):
        return {
            "type": "voice_state",
            "state": "speaking",
            "role": "",
            "phase": "",
            "text": "",
        }
    if isinstance(frame, TTSStoppedFrame):
        return {
            "type": "voice_state",
            "state": "listening",
            "role": "",
            "phase": "",
            "text": "",
        }
    return None


class VoiceEventProcessor(FrameProcessor):
    """Mirror selected downstream frames to a WebRTC application data channel."""

    def __init__(self, send_event: Callable[[dict[str, str | int]], None]) -> None:
        super().__init__()
        self._send_event = send_event

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Forward media unchanged after publishing a matching browser event."""
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            event = event_from_frame(frame)
            if event is not None:
                self._send_event(cast(dict[str, str | int], event))
        await self.push_frame(frame, direction)
