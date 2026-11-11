"""Translate public Pipecat conversation frames into browser-safe voice events."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypedDict, cast

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputTransportMessageUrgentFrame,
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
    """Publish one selected category of browser state for each media frame."""

    def __init__(
        self,
        send_event: Callable[[dict[str, str | int]], None],
        *,
        transcript_roles: frozenset[str] = frozenset({"user", "assistant"}),
        include_voice_state: bool = True,
        ordered_transport_events: bool = False,
    ) -> None:
        super().__init__()
        self._send_event = send_event
        self._transcript_roles = transcript_roles
        self._include_voice_state = include_voice_state
        self._ordered_transport_events = ordered_transport_events

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Forward media unchanged after publishing a matching browser event."""
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            event = event_from_frame(frame)
            if event is not None and self._should_publish(event):
                browser_event = cast(dict[str, str | int], event)
                if self._ordered_transport_events:
                    # WebSocket captions and PCM must share the transport queue.
                    # Scheduling websocket.send_text separately allows stale PCM
                    # to overtake its cancellation and subtitle controls.
                    await self.push_frame(
                        OutputTransportMessageUrgentFrame(message=browser_event), direction
                    )
                else:
                    self._send_event(browser_event)
        await self.push_frame(frame, direction)

    def _should_publish(self, event: VoiceEvent) -> bool:
        if event["type"] == "voice_state":
            return self._include_voice_state
        return event["role"] in self._transcript_roles
