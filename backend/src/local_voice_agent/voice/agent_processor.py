"""Translate finalized media transcription into Agent Core response frames."""

from __future__ import annotations

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from local_voice_agent.agent.core import (
    AgentCore,
    AgentRequest,
    AgentResponseKind,
    ConversationTurn,
)


class PipecatAgentProcessor(FrameProcessor):
    """Keep media framing in Pipecat while the Agent Core owns local text generation."""

    def __init__(self, agent: AgentCore, history_limit: int = 20) -> None:
        super().__init__()
        self._agent = agent
        self._history_limit = history_limit
        self._history: list[ConversationTurn] = []
        self._active_utterance_id: str | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Consume user transcription and preserve all unrelated media frames unchanged."""
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, InterruptionFrame):
            self._active_utterance_id = None
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, TranscriptionFrame):
            await self._respond_to_transcription(frame)
            return
        await self.push_frame(frame, direction)

    async def _respond_to_transcription(self, frame: TranscriptionFrame) -> None:
        utterance_id = f"agent:{frame.id}"
        self._active_utterance_id = utterance_id
        language = frame.language.value if frame.language is not None else "zh"
        request = AgentRequest(
            utterance_id=utterance_id,
            transcript=frame.text,
            language=language,
            history=tuple(self._history),
        )
        self._append_history(ConversationTurn("user", frame.text, utterance_id))
        assistant_parts: list[str] = []
        await self.push_frame(LLMFullResponseStartFrame())
        async for event in self._agent.respond(request):
            if event.utterance_id != utterance_id or self._active_utterance_id != utterance_id:
                continue
            if event.kind == AgentResponseKind.TEXT:
                assistant_parts.append(event.text)
                await self.push_frame(LLMTextFrame(event.text))
            elif event.kind == AgentResponseKind.ERROR:
                assistant_parts.append(event.text)
                await self.push_frame(LLMTextFrame(event.text))
            elif event.kind == AgentResponseKind.END:
                break
        if self._active_utterance_id != utterance_id:
            return
        assistant_text = "".join(assistant_parts)
        if assistant_text:
            self._append_history(ConversationTurn("assistant", assistant_text, utterance_id))
        self._active_utterance_id = None
        await self.push_frame(LLMFullResponseEndFrame())

    def _append_history(self, turn: ConversationTurn) -> None:
        self._history.append(turn)
        overflow = len(self._history) - self._history_limit
        if overflow > 0:
            del self._history[:overflow]
