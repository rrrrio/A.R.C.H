"""Server-side proof that browser microphone PCM reached the media pipeline."""

from __future__ import annotations

from collections.abc import Callable

from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class InputReadyProcessor(FrameProcessor):
    """Publish one readiness event after the first decoded microphone frame arrives."""

    def __init__(self, send_event: Callable[[dict[str, str | int]], None]) -> None:
        super().__init__()
        self._send_event = send_event
        self._announced = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not self._announced and direction == FrameDirection.DOWNSTREAM and isinstance(
            frame, InputAudioRawFrame
        ):
            self._announced = True
            self._send_event({"type": "voice_input_ready"})
        await self.push_frame(frame, direction)
