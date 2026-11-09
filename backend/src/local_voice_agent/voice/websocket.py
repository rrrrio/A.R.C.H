"""Browser PCM framing for the same-origin FastAPI WebSocket transport."""

from __future__ import annotations

import json

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer


class BrowserPCMSerializer(FrameSerializer):
    """Encode raw PCM media and bounded JSON control messages for the browser."""

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            return json.dumps(frame.message, separators=(",", ":"))
        if isinstance(frame, InterruptionFrame):
            return '{"type":"audio_interrupted"}'
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if not isinstance(data, bytes) or not data or len(data) % 2 or len(data) > 131_072:
            return None
        return InputAudioRawFrame(audio=data, sample_rate=16000, num_channels=1)
