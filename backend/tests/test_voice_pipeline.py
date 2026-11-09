"""Tests for the browser voice media pipeline defaults."""

from local_voice_agent.voice.pipeline import LAN_VAD_PARAMS


def test_lan_vad_accepts_quieter_microphones() -> None:
    """Keep the LAN microphone gate below Pipecat's conservative defaults."""
    assert LAN_VAD_PARAMS.confidence == 0.6
    assert LAN_VAD_PARAMS.min_volume == 0.25
