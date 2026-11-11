"""Frame-level tests for routing live Pipecat TTS through OutputMux."""

import asyncio

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageUrgentFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from arch.voice.emotion import EmotionFrame
from arch.voice.pipecat_output import PipecatOutputMuxProcessor


class InspectingOutputMuxProcessor(PipecatOutputMuxProcessor):
    """Capture downstream frames without a running Pipecat worker."""

    def __init__(self) -> None:
        super().__init__(24000)
        self.forwarded: list[Frame] = []

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        """Record a frame that would otherwise enter the transport."""
        assert direction == FrameDirection.DOWNSTREAM
        self.forwarded.append(frame)

    async def _start_interruption(self) -> None:
        """Avoid worker queue setup that is unrelated to this frame-routing unit test."""


def test_output_mux_processor_replaces_tts_pcm_and_drops_late_audio_after_interrupt() -> None:
    """Old Pipecat chunks cannot reach WebRTC after a new user interruption."""
    processor = InspectingOutputMuxProcessor()

    async def exercise() -> None:
        await processor.process_frame(TTSStartedFrame(context_id="old"), FrameDirection.DOWNSTREAM)
        old_audio = TTSAudioRawFrame(b"\x01\x00", 24000, 1, context_id="old")
        old_audio.pts = 123
        await processor.process_frame(old_audio, FrameDirection.DOWNSTREAM)
        await processor.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        await processor.process_frame(TTSStartedFrame(context_id="old"), FrameDirection.DOWNSTREAM)
        await processor.process_frame(old_audio, FrameDirection.DOWNSTREAM)
        await processor.process_frame(TTSStoppedFrame(context_id="old"), FrameDirection.DOWNSTREAM)
        await processor.process_frame(TTSStartedFrame(context_id="new"), FrameDirection.DOWNSTREAM)
        new_audio = TTSAudioRawFrame(b"\x02\x00", 24000, 1, context_id="new")
        await processor.process_frame(new_audio, FrameDirection.DOWNSTREAM)

    asyncio.run(exercise())

    assert isinstance(processor.forwarded[0], OutputTransportMessageUrgentFrame)
    assert processor.forwarded[0].message == {
        "type": "audio_started",
        "utterance_id": "tts:old",
    }
    assert isinstance(processor.forwarded[1], TTSStartedFrame)
    output = processor.forwarded[2]
    assert isinstance(output, OutputAudioRawFrame)
    assert not isinstance(output, TTSAudioRawFrame)
    assert output.audio == b"\x01\x00"
    assert output.sample_rate == 24000
    assert output.pts == 123
    assert output.metadata["utterance_id"] == "tts:old"
    assert isinstance(processor.forwarded[3], OutputTransportMessageUrgentFrame)
    assert processor.forwarded[3].message == {"type": "audio_cancelled", "utterance_id": "tts:old"}
    assert isinstance(processor.forwarded[4], InterruptionFrame)
    assert isinstance(processor.forwarded[5], OutputTransportMessageUrgentFrame)
    assert processor.forwarded[5].message == {"type": "audio_started", "utterance_id": "tts:new"}
    replacement = processor.forwarded[7]
    assert isinstance(replacement, OutputAudioRawFrame)
    assert replacement.audio == b"\x02\x00"
    assert len(processor.forwarded) == 8


def test_output_mux_processor_only_ends_the_active_tts_context() -> None:
    """A stale TTS stop cannot change the browser state for a replacement response."""
    processor = InspectingOutputMuxProcessor()

    async def exercise() -> None:
        await processor.process_frame(TTSStartedFrame(context_id="old"), FrameDirection.DOWNSTREAM)
        await processor.process_frame(TTSStartedFrame(context_id="new"), FrameDirection.DOWNSTREAM)
        await processor.process_frame(TTSStoppedFrame(context_id="old"), FrameDirection.DOWNSTREAM)
        await processor.process_frame(TTSStoppedFrame(context_id="new"), FrameDirection.DOWNSTREAM)

    asyncio.run(exercise())

    assert [type(frame) for frame in processor.forwarded] == [
        OutputTransportMessageUrgentFrame,
        TTSStartedFrame,
        OutputTransportMessageUrgentFrame,
        TTSStartedFrame,
        TTSStoppedFrame,
    ]


def test_output_mux_processor_uses_bounded_emotion_for_next_tts_context() -> None:
    """Expression metadata affects a future output context and resets on interruption."""
    processor = InspectingOutputMuxProcessor()
    beginnings: list[tuple[str, int, str]] = []

    async def begin(utterance_id: str, sample_rate: int, emotion: str) -> None:
        beginnings.append((utterance_id, sample_rate, emotion))

    processor._mux.begin = begin  # type: ignore[assignment]

    async def exercise() -> None:
        await processor.process_frame(EmotionFrame(emotion="warm"), FrameDirection.DOWNSTREAM)
        await processor.process_frame(TTSStartedFrame(context_id="warm"), FrameDirection.DOWNSTREAM)
        await processor.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        await processor.process_frame(
            TTSStartedFrame(context_id="neutral"), FrameDirection.DOWNSTREAM
        )

    asyncio.run(exercise())

    assert beginnings == [
        ("tts:warm", 24000, "warm"),
        ("tts:neutral", 24000, "neutral"),
    ]
