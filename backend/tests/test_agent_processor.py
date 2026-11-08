"""Tests for the Pipecat media-to-Agent Core bridge."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from local_voice_agent.agent.core import AgentRequest, AgentResponseEvent, AgentResponseKind
from local_voice_agent.memory.store import MemoryStore
from local_voice_agent.voice.agent_processor import PipecatAgentProcessor


class FakeAgent:
    """Return deterministic events while recording media-independent requests."""

    def __init__(self) -> None:
        self.requests: list[AgentRequest] = []

    async def respond(self, request: AgentRequest) -> AsyncIterator[AgentResponseEvent]:
        self.requests.append(request)
        yield AgentResponseEvent(request.utterance_id, AgentResponseKind.TEXT, "回答")
        yield AgentResponseEvent(request.utterance_id, AgentResponseKind.END)


class InspectingAgentProcessor(PipecatAgentProcessor):
    """Capture generated frames without a full media worker."""

    def __init__(self, agent: FakeAgent, memory_store: MemoryStore | None = None) -> None:
        super().__init__(agent, memory_store=memory_store)
        self.forwarded: list[Frame] = []

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        """Record direct downstream output for assertions."""
        assert direction == FrameDirection.DOWNSTREAM
        self.forwarded.append(frame)


def test_agent_processor_converts_transcription_to_response_frames_and_history() -> None:
    """Media code sends only a typed request while Agent Core owns turn history."""
    agent = FakeAgent()
    processor = InspectingAgentProcessor(agent)

    async def exercise() -> None:
        await processor.process_frame(
            TranscriptionFrame("你好", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )
        await processor.process_frame(
            TranscriptionFrame("再说一次", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )

    asyncio.run(exercise())

    assert [type(frame) for frame in processor.forwarded] == [
        LLMFullResponseStartFrame,
        LLMTextFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
        LLMFullResponseEndFrame,
    ]
    assert [request.transcript for request in agent.requests] == ["你好", "再说一次"]
    assert [(turn.role, turn.text) for turn in agent.requests[1].history] == [
        ("user", "你好"),
        ("assistant", "回答"),
    ]


def test_agent_processor_persists_completed_turns_but_not_pending_memory(tmp_path: Path) -> None:
    """Session history survives in SQLite while the memory candidate table remains untouched."""
    agent = FakeAgent()
    store = MemoryStore(tmp_path / "voice.sqlite3")
    store.migrate()
    processor = InspectingAgentProcessor(agent, memory_store=store)

    async def exercise() -> None:
        await processor.process_frame(
            TranscriptionFrame("持久化这一轮", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )

    asyncio.run(exercise())

    conversation_id = processor.conversation_id
    assert conversation_id is not None
    persisted = [
        (message.role, message.content) for message in store.list_messages(conversation_id)
    ]
    assert persisted == [
        ("user", "持久化这一轮"),
        ("assistant", "回答"),
    ]
