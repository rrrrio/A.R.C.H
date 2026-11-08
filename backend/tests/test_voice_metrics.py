"""Tests for local-only monotonic latency telemetry."""

from pipecat.frames.frames import (
    LLMTextFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
)

from local_voice_agent.observability.metrics import LatencyStage, LatencyTracker
from local_voice_agent.voice.metrics import VoiceLatencyProcessor


class ManualClock:
    """Advance a deterministic monotonic clock for latency tests."""

    def __init__(self) -> None:
        self.now_ns = 0

    def __call__(self) -> int:
        return self.now_ns


def test_latency_tracker_records_ordered_monotonic_turn_stages() -> None:
    """A turn emits each ordered stage once and never uses wall-clock timestamps."""
    clock = ManualClock()
    tracker = LatencyTracker(clock)

    assert tracker.begin_speech()[0].as_event() == {
        "type": "latency",
        "utterance_id": "turn-1",
        "stage": "speech-start",
        "elapsed_ms": 0,
    }
    assert tracker.record(LatencyStage.LLM_FIRST_TOKEN) is None
    clock.now_ns = 12_999_999
    assert tracker.record(LatencyStage.SPEECH_END).elapsed_ms == 12  # type: ignore[union-attr]
    clock.now_ns = 27_000_000
    assert tracker.record(LatencyStage.ASR_FINAL).elapsed_ms == 27  # type: ignore[union-attr]
    assert tracker.record(LatencyStage.ASR_FINAL) is None
    clock.now_ns = 41_000_000
    assert tracker.record(LatencyStage.LLM_FIRST_TOKEN).elapsed_ms == 41  # type: ignore[union-attr]
    clock.now_ns = 58_000_000
    assert tracker.record(LatencyStage.TTS_FIRST_BYTE).elapsed_ms == 58  # type: ignore[union-attr]
    clock.now_ns = 65_000_000
    assert tracker.record(LatencyStage.PLAYBACK_FIRST_AUDIO).elapsed_ms == 65  # type: ignore[union-attr]


def test_new_speech_closes_old_playback_and_rejects_old_stage_callbacks() -> None:
    """An interruption cannot let an old response advance the replacement turn."""
    clock = ManualClock()
    tracker = LatencyTracker(clock)
    tracker.begin_speech()
    for stage in (
        LatencyStage.SPEECH_END,
        LatencyStage.ASR_FINAL,
        LatencyStage.LLM_FIRST_TOKEN,
        LatencyStage.TTS_FIRST_BYTE,
        LatencyStage.PLAYBACK_FIRST_AUDIO,
    ):
        clock.now_ns += 1_000_000
        assert tracker.record(stage) is not None

    clock.now_ns = 20_000_000
    interrupted, replacement = tracker.begin_speech()
    assert interrupted.stage == LatencyStage.PLAYBACK_STOPPED
    assert interrupted.utterance_id == "turn-1"
    assert replacement.utterance_id == "turn-2"
    assert tracker.record(LatencyStage.TTS_FIRST_BYTE) is None


def test_frame_processors_only_observe_their_assigned_pipeline_boundary() -> None:
    """Repeated downstream frames cannot duplicate a latency stage at another boundary."""
    clock = ManualClock()
    tracker = LatencyTracker(clock)
    asr = VoiceLatencyProcessor(
        lambda _: None, tracker, stages=frozenset({LatencyStage.ASR_FINAL})
    )
    llm = VoiceLatencyProcessor(
        lambda _: None, tracker, stages=frozenset({LatencyStage.LLM_FIRST_TOKEN})
    )
    tts = VoiceLatencyProcessor(
        lambda _: None, tracker, stages=frozenset({LatencyStage.TTS_FIRST_BYTE})
    )
    output = VoiceLatencyProcessor(
        lambda _: None, tracker, stages=frozenset({LatencyStage.PLAYBACK_FIRST_AUDIO})
    )

    tracker.begin_speech()
    assert tracker.record(LatencyStage.SPEECH_END) is not None
    asr_sample = asr._samples_for(TranscriptionFrame("hello", "user", "timestamp"))[0]
    assert asr_sample.stage == LatencyStage.ASR_FINAL
    assert llm._samples_for(LLMTextFrame("hello"))[0].stage == LatencyStage.LLM_FIRST_TOKEN
    tts_sample = tts._samples_for(TTSAudioRawFrame(b"\x00\x00", 24000, 1))[0]
    assert tts_sample.stage == LatencyStage.TTS_FIRST_BYTE
    output_sample = output._samples_for(OutputAudioRawFrame(b"\x00\x00", 24000, 1))[0]
    assert output_sample.stage == LatencyStage.PLAYBACK_FIRST_AUDIO
    assert output._samples_for(OutputAudioRawFrame(b"\x00\x00", 24000, 1)) == ()
