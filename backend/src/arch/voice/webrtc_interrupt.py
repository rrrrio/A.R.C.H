"""Clear SmallWebRTC's transport-owned PCM queue when a turn is interrupted."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from pipecat.frames.frames import Frame, InterruptionFrame, VADUserStartedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


def clear_queued_webrtc_audio(output_transport: object) -> bool:
    """Discard PCM that Pipecat has already handed to the active WebRTC audio track.

    Pipecat clears its generic output queue on ``InterruptionFrame``, but the
    SmallWebRTC transport keeps a second queue of 10 ms PCM segments. Its public
    API has no flush operation, so this capability check deliberately targets only
    the installed transport's active track and remains a no-op for other outputs.
    """
    client = getattr(output_transport, "_client", None)
    track = getattr(client, "_audio_output_track", None)
    queue = getattr(track, "_chunk_queue", None)
    if not isinstance(queue, deque):
        return False

    while queue:
        _, completion = queue.popleft()
        if completion is not None and not completion.done():
            completion.set_result(False)
    return True


class WebRTCAudioInterruptionProcessor(FrameProcessor):
    """Flush transport-owned browser audio before forwarding an interruption."""

    def __init__(self, clear_queued_audio: Callable[[], bool]) -> None:
        super().__init__()
        self._clear_queued_audio = clear_queued_audio

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Clear only downstream interruptible output; preserve every other frame."""
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, InterruptionFrame):
            self._clear_queued_audio()
        await self.push_frame(frame, direction)


class BargeInInterruptionProcessor(FrameProcessor):
    """Turn the public VAD speech-start signal into an output cancellation frame.

    Pipecat 1.15 broadcasts ``VADUserStartedSpeakingFrame`` for UI state but
    does not also broadcast an ``InterruptionFrame``. The explicit downstream
    frame is what cancels the Agent, TTS, output mux, and transport together.
    """

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(
            frame, VADUserStartedSpeakingFrame
        ):
            await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
