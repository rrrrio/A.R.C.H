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

    def __init__(self) -> None:
        self._active_utterance_id: str | None = None
        self._cancelled_utterance_ids: set[str] = set()

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, OutputAudioRawFrame):
            utterance_id = self._active_utterance_id
            if utterance_id is None:
                return None
            identifier = utterance_id.encode("utf-8")
            if not identifier or len(identifier) > 255:
                return None
            return b"APCM" + bytes((len(identifier),)) + identifier + frame.audio
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            self._apply_audio_generation(frame.message)
            return json.dumps(frame.message, separators=(",", ":"))
        if isinstance(frame, InterruptionFrame):
            self._active_utterance_id = None
            return '{"type":"audio_interrupted"}'
        return None

    def _apply_audio_generation(self, message: object) -> None:
        if not isinstance(message, dict):
            return
        message_type = message.get("type")
        utterance_id = message.get("utterance_id")
        if not isinstance(utterance_id, str):
            return
        if message_type == "audio_cancelled":
            self._cancelled_utterance_ids.add(utterance_id)
            if self._active_utterance_id == utterance_id:
                self._active_utterance_id = None
        elif message_type == "audio_started" and utterance_id not in self._cancelled_utterance_ids:
            self._active_utterance_id = utterance_id

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if not isinstance(data, bytes) or not data or len(data) % 2 or len(data) > 131_072:
            return None
        return InputAudioRawFrame(audio=data, sample_rate=16000, num_channels=1)
