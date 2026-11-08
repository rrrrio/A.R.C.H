"""Translate finalized media transcription into Agent Core response frames."""

from __future__ import annotations

import asyncio

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
from local_voice_agent.memory.context import bounded_approved_memories
from local_voice_agent.memory.extractor import MemoryCandidateExtractor
from local_voice_agent.memory.store import MemoryStore, StoredMessage
from local_voice_agent.rag.retriever import Retriever


class PipecatAgentProcessor(FrameProcessor):
    """Keep media framing in Pipecat while the Agent Core owns local text generation."""

    def __init__(
        self,
        agent: AgentCore,
        history_limit: int = 20,
        memory_store: MemoryStore | None = None,
        candidate_extractor: MemoryCandidateExtractor | None = None,
        retriever: Retriever | None = None,
    ) -> None:
        super().__init__()
        self._agent = agent
        self._history_limit = history_limit
        self._history: list[ConversationTurn] = []
        self._active_utterance_id: str | None = None
        self._memory_store = memory_store
        self._candidate_extractor = candidate_extractor
        self._retriever = retriever
        self._candidate_task: asyncio.Task[None] | None = None
        self._conversation_id = (
            memory_store.create_conversation() if memory_store is not None else None
        )

    @property
    def conversation_id(self) -> str | None:
        """Return the durable conversation identifier owned by this media session."""
        return self._conversation_id

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Consume user transcription and preserve all unrelated media frames unchanged."""
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, InterruptionFrame):
            self._cancel_candidate_extraction()
            self._active_utterance_id = None
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, TranscriptionFrame):
            self._cancel_candidate_extraction()
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
            history=await self._history_for_request(),
            approved_memories=await self._approved_memories_for_request(),
            retrieved_context=await self._retrieved_context_for_request(frame.text),
        )
        stored_user = await self._record_turn(ConversationTurn("user", frame.text, utterance_id))
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
            await self._record_turn(ConversationTurn("assistant", assistant_text, utterance_id))
            self._start_candidate_extraction(stored_user, language)
        self._active_utterance_id = None
        await self.push_frame(LLMFullResponseEndFrame())

    async def _history_for_request(self) -> tuple[ConversationTurn, ...]:
        if self._memory_store is None or self._conversation_id is None:
            return tuple(self._history)
        messages = await asyncio.to_thread(
            self._memory_store.list_messages, self._conversation_id, self._history_limit
        )
        return tuple(
            ConversationTurn(message.role, message.content, message.utterance_id)
            for message in messages
        )

    async def _approved_memories_for_request(self) -> tuple[str, ...]:
        if self._memory_store is None:
            return ()
        candidates = await asyncio.to_thread(self._memory_store.list_candidates, "approved", 12)
        return bounded_approved_memories(candidate.content for candidate in candidates)

    async def _retrieved_context_for_request(self, transcript: str) -> tuple[str, ...]:
        if self._retriever is None:
            return ()
        try:
            return await asyncio.to_thread(self._retriever.retrieve, transcript)
        except Exception:
            return ()

    async def _record_turn(self, turn: ConversationTurn) -> StoredMessage | None:
        if self._memory_store is not None and self._conversation_id is not None:
            stored = await asyncio.to_thread(
                self._memory_store.append_message,
                self._conversation_id,
                turn.utterance_id,
                turn.role,
                turn.text,
            )
        else:
            stored = None
        self._history.append(turn)
        overflow = len(self._history) - self._history_limit
        if overflow > 0:
            del self._history[:overflow]
        return stored

    def _start_candidate_extraction(self, message: StoredMessage | None, language: str) -> None:
        if message is None or self._candidate_extractor is None or self._memory_store is None:
            return
        self._candidate_task = asyncio.create_task(self._extract_candidates(message, language))

    def _cancel_candidate_extraction(self) -> None:
        if self._candidate_task is not None:
            self._candidate_task.cancel()
            self._candidate_task = None

    async def _extract_candidates(self, message: StoredMessage, language: str) -> None:
        if self._candidate_extractor is None or self._memory_store is None:
            return
        try:
            candidates = await self._candidate_extractor.extract(message.content, language)
            for content in candidates:
                await asyncio.to_thread(self._memory_store.create_candidate, message.id, content)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def cleanup(self) -> None:
        """Cancel an idle extraction before Pipecat releases this session's resources."""
        task = self._candidate_task
        self._cancel_candidate_extraction()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await super().cleanup()  # type: ignore[no-untyped-call]
