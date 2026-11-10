"""Tests for media-to-browser status and caption event mapping."""

from pipecat.frames.frames import (
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    VADUserStartedSpeakingFrame,
)

from arch.voice.events import event_from_frame


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
