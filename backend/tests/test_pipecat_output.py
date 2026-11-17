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

from arch.avatar.media import AvatarMediaAnswer, AvatarMediaService
from arch.avatar.media_adapter import AvatarMediaAdapter
from arch.voice.emotion import EmotionFrame
from arch.voice.pipecat_output import PipecatOutputMuxProcessor


class InspectingOutputMuxProcessor(PipecatOutputMuxProcessor):
    """Capture downstream frames without a running Pipecat worker."""

    def __init__(self, avatar_observer=None) -> None:
        super().__init__(24000, avatar_observer)
        self.forwarded: list[Frame] = []

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        """Record a frame that would otherwise enter the transport."""
        assert direction == FrameDirection.DOWNSTREAM
        self.forwarded.append(frame)

    async def _start_interruption(self) -> None:
        """Avoid worker queue setup that is unrelated to this frame-routing unit test."""


class WholeAnswerAvatar:
    def __init__(self, *, submitted: bool) -> None:
        self.submitted = submitted
        self.calls: list[str] = []

    def is_active(self) -> bool:
        return True

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        self.calls.append(f"begin:{utterance_id}:{sample_rate}:{emotion}")

    async def push_audio(self, utterance_id: str, audio: bytes, timestamp_ns: int) -> None:
        self.calls.append(f"audio:{utterance_id}:{audio!r}:{timestamp_ns}")

    async def push_event(self, utterance_id: str, event: str) -> None:
        self.calls.append(f"event:{utterance_id}:{event}")

    async def cancel(self, utterance_id: str) -> None:
        self.calls.append(f"cancel:{utterance_id}")

    async def end(self, utterance_id: str) -> bool:
        self.calls.append(f"end:{utterance_id}")
        return self.submitted


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


def test_avatar_submission_owns_complete_answer_without_direct_audio_duplication() -> None:
    """A successful runtime submission suppresses ordinary PCM before TTS stops."""
    avatar = WholeAnswerAvatar(submitted=True)
    processor = InspectingOutputMuxProcessor()
    processor._avatar_observer = avatar

    async def exercise() -> None:
        await processor.process_frame(
            TTSStartedFrame(context_id="answer"), FrameDirection.DOWNSTREAM
        )
        audio = TTSAudioRawFrame(b"\x01\x00", 24000, 1, context_id="answer")
        audio.pts = 7
        await processor.process_frame(audio, FrameDirection.DOWNSTREAM)
        await processor.process_frame(
            TTSStoppedFrame(context_id="answer"), FrameDirection.DOWNSTREAM
        )

    asyncio.run(exercise())

    assert not any(isinstance(frame, OutputAudioRawFrame) for frame in processor.forwarded)
    assert avatar.calls[-1] == "end:tts:answer"


def test_avatar_submission_failure_releases_pcm_before_tts_context_closes() -> None:
    """Runtime failure falls back synchronously instead of losing the buffered answer."""
    avatar = WholeAnswerAvatar(submitted=False)
    processor = InspectingOutputMuxProcessor()
    processor._avatar_observer = avatar

    async def exercise() -> None:
        await processor.process_frame(
            TTSStartedFrame(context_id="answer"), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            TTSAudioRawFrame(b"\x02\x00", 24000, 1, context_id="answer"),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(
            TTSStoppedFrame(context_id="answer"), FrameDirection.DOWNSTREAM
        )

    asyncio.run(exercise())

    audio_index = next(
        index
        for index, frame in enumerate(processor.forwarded)
        if isinstance(frame, OutputAudioRawFrame)
    )
    stop_index = next(
        index
        for index, frame in enumerate(processor.forwarded)
        if isinstance(frame, TTSStoppedFrame)
    )
    assert audio_index < stop_index


def test_real_adapter_runtime_failure_releases_the_complete_answer_once_before_stop() -> None:
    """The concrete private runtime adapter cannot turn a failed answer into silence."""
    class FailingRuntime:
        async def create_session(
            self, *, ticket: str, session_id: str, offer_sdp: str
        ) -> AvatarMediaAnswer:
            del ticket, session_id, offer_sdp
            return AvatarMediaAnswer(sdp="answer", pc_id="peer")

        async def close_session(self, *, session_id: str) -> None:
            del session_id

        async def submit_utterance(
            self, *, session_id: str, utterance_id: str, pcm_24khz: bytes
        ) -> None:
            del session_id, utterance_id, pcm_24khz
            raise RuntimeError("synthetic runtime failure")

        async def cancel_utterance(self, *, session_id: str, utterance_id: str) -> None:
            del session_id, utterance_id

    service = AvatarMediaService(runtime=FailingRuntime())
    adapter = AvatarMediaAdapter(service, "session")
    processor = InspectingOutputMuxProcessor(adapter)

    async def exercise() -> None:
        await service.offer("session", "offer")
        await processor.process_frame(
            TTSStartedFrame(context_id="answer"), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            TTSAudioRawFrame(b"first", 24000, 1, context_id="answer"),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(
            TTSAudioRawFrame(b"second", 24000, 1, context_id="answer"),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(
            TTSStoppedFrame(context_id="answer"), FrameDirection.DOWNSTREAM
        )

    asyncio.run(exercise())
    frames = [frame for frame in processor.forwarded if isinstance(frame, OutputAudioRawFrame)]
    stop_index = next(
        index
        for index, frame in enumerate(processor.forwarded)
        if isinstance(frame, TTSStoppedFrame)
    )
    assert [frame.audio for frame in frames] == [b"first", b"second"]
    assert max(processor.forwarded.index(frame) for frame in frames) < stop_index


def test_interruption_discards_buffered_avatar_pcm_even_without_a_stop_frame() -> None:
    """A cancelled TTS producer cannot retain a whole buffered answer in process memory."""
    avatar = WholeAnswerAvatar(submitted=True)
    processor = InspectingOutputMuxProcessor()
    processor._avatar_observer = avatar

    async def exercise() -> None:
        await processor.process_frame(
            TTSStartedFrame(context_id="answer"), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            TTSAudioRawFrame(b"\x01\x00", 24000, 1, context_id="answer"),
            FrameDirection.DOWNSTREAM,
        )
        await processor.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)

    asyncio.run(exercise())

    assert processor._avatar_audio == {}
    assert "cancel:tts:answer" in avatar.calls
