"""Tests for media-to-browser status and caption event mapping."""

import asyncio

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputTransportMessageUrgentFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from arch.voice.events import VoiceEventProcessor, event_from_frame


class InspectingVoiceEventProcessor(VoiceEventProcessor):
    """Capture ordered control frames without a running Pipecat worker."""

    def __init__(self) -> None:
        super().__init__(lambda _event: None, ordered_transport_events=True)
        self.forwarded: list[Frame] = []

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        assert direction == FrameDirection.DOWNSTREAM
        self.forwarded.append(frame)


def test_ordered_transport_events_precede_their_media_frame() -> None:
    """Fallback captions share ordering with PCM controls instead of background tasks."""
    processor = InspectingVoiceEventProcessor()

    asyncio.run(processor.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM))

    control, original = processor.forwarded
    assert isinstance(control, OutputTransportMessageUrgentFrame)
    assert control.message == {
        "type": "transcript", "state": "thinking", "role": "assistant", "phase": "start", "text": ""
    }
    assert original.__class__ is LLMFullResponseStartFrame


def test_voice_event_mapping_covers_status_and_captions() -> None:
    """Public Pipecat frames map to the small browser event protocol."""
    assert event_from_frame(VADUserStartedSpeakingFrame()) == {
        "type": "voice_state",
        "state": "user-speaking",
        "role": "",
        "phase": "",
        "text": "",
    }
    assert event_from_frame(TranscriptionFrame("你好", "user", "2026-11-08T00:00:00Z")) == {
        "type": "transcript",
        "state": "thinking",
        "role": "user",
        "phase": "final",
        "text": "你好",
    }
    assert event_from_frame(LLMFullResponseStartFrame()) == {
        "type": "transcript",
        "state": "thinking",
        "role": "assistant",
        "phase": "start",
        "text": "",
    }
    assert event_from_frame(LLMTextFrame("你好！")) == {
        "type": "transcript",
        "state": "thinking",
        "role": "assistant",
        "phase": "delta",
        "text": "你好！",
    }
    assert event_from_frame(TTSStartedFrame()) == {
        "type": "voice_state",
        "state": "speaking",
        "role": "",
        "phase": "",
        "text": "",
    }
