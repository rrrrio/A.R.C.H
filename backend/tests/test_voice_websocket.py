"""Tests for the browser PCM WebSocket frame contract."""

from __future__ import annotations

import asyncio

from pipecat.frames.frames import (
    InputAudioRawFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
)

from arch.voice.websocket import BrowserPCMSerializer


def test_serializer_decodes_only_valid_pcm16_microphone_bytes() -> None:
    """Browser input is bounded PCM16 mono at the pipeline's 16 kHz rate."""
    serializer = BrowserPCMSerializer()

    async def exercise() -> None:
        frame = await serializer.deserialize(b"\x01\x00\xff\x7f")
        assert isinstance(frame, InputAudioRawFrame)
        assert frame.audio == b"\x01\x00\xff\x7f"
        assert frame.sample_rate == 16000
        assert frame.num_channels == 1
        assert await serializer.deserialize("not-media") is None
        assert await serializer.deserialize(b"\x00") is None
        assert await serializer.deserialize(b"") is None

    asyncio.run(exercise())


def test_serializer_encodes_pcm_with_utterance_ownership_and_browser_events() -> None:
    """The browser receives response-bound PCM and compact JSON on the same socket."""
    serializer = BrowserPCMSerializer()

    async def exercise() -> None:
        audio = OutputAudioRawFrame(audio=b"\x02\x00", sample_rate=24000, num_channels=1)
        started = OutputTransportMessageFrame(
            message={"type": "audio_started", "utterance_id": "tts:turn-1"}
        )
        event = OutputTransportMessageFrame(message={"type": "voice_ready"})
        assert await serializer.serialize(audio) is None
        assert await serializer.serialize(started) == (
            '{"type":"audio_started","utterance_id":"tts:turn-1"}'
        )
        assert await serializer.serialize(audio) == b"APCM\x0atts:turn-1\x02\x00"
        assert await serializer.serialize(event) == '{"type":"voice_ready"}'

    asyncio.run(exercise())
