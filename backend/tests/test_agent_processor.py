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

from local_voice_agent.agent.core import (
    AgentCore,
    AgentRequest,
    AgentResponseEvent,
    AgentResponseKind,
    ToolRequest,
    ToolResult,
)
from local_voice_agent.memory.extractor import MemoryCandidateExtractor
from local_voice_agent.memory.store import MemoryStore
from local_voice_agent.rag.retriever import Retriever
from local_voice_agent.tools.gateway import ToolOutcome
from local_voice_agent.tools.terminal import TerminalResult
from local_voice_agent.voice.agent_processor import PipecatAgentProcessor, ToolDispatcher


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

    def __init__(
        self,
        agent: AgentCore,
        memory_store: MemoryStore | None = None,
        candidate_extractor: MemoryCandidateExtractor | None = None,
        retriever: Retriever | None = None,
        tool_gateway: ToolDispatcher | None = None,
    ) -> None:
        super().__init__(
            agent,
            memory_store=memory_store,
            candidate_extractor=candidate_extractor,
            retriever=retriever,
            tool_gateway=tool_gateway,
        )
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


def test_agent_processor_accepts_a_runtime_string_language_code() -> None:
    """Whisper may provide a string code despite Pipecat's enum-oriented annotation."""
    agent = FakeAgent()
    processor = InspectingAgentProcessor(agent)
    frame = TranscriptionFrame("你好", "user", "timestamp")
    frame.language = "zh"  # type: ignore[assignment]

    asyncio.run(processor.process_frame(frame, FrameDirection.DOWNSTREAM))

    assert agent.requests[0].language == "zh"


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


class FakeCandidateExtractor:
    """Return one fixed candidate after the media response has completed."""

    async def extract(self, transcript: str, language: str) -> tuple[str, ...]:
        assert transcript == "持久化这一轮"
        assert language == "zh"
        return ("用户偏好简洁回答",)


def test_agent_processor_extracts_pending_memory_after_a_completed_answer(tmp_path: Path) -> None:
    """Candidate extraction is background work and never inserts an approved memory directly."""
    store = MemoryStore(tmp_path / "voice.sqlite3")
    store.migrate()
    processor = InspectingAgentProcessor(
        FakeAgent(), memory_store=store, candidate_extractor=FakeCandidateExtractor()
    )

    async def exercise() -> None:
        await processor.process_frame(
            TranscriptionFrame("持久化这一轮", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )
        assert processor._candidate_task is not None
        await processor._candidate_task

    asyncio.run(exercise())

    candidates = store.list_candidates("pending")
    assert [(candidate.content, candidate.status) for candidate in candidates] == [
        ("用户偏好简洁回答", "pending")
    ]


class BlockingCandidateExtractor:
    """Expose cancellation of extraction that has yielded the real-time media path."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def extract(self, transcript: str, language: str) -> tuple[str, ...]:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return ()


def test_agent_processor_cancels_background_extraction_when_realtime_work_resumes(
    tmp_path: Path,
) -> None:
    """The interruption/transcription path can stop extraction before it writes a candidate."""
    store = MemoryStore(tmp_path / "voice.sqlite3")
    store.migrate()
    extractor = BlockingCandidateExtractor()
    processor = InspectingAgentProcessor(
        FakeAgent(), memory_store=store, candidate_extractor=extractor
    )

    async def exercise() -> None:
        await processor.process_frame(
            TranscriptionFrame("持久化这一轮", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )
        await extractor.started.wait()
        processor._cancel_candidate_extraction()
        await extractor.cancelled.wait()

    asyncio.run(exercise())

    assert store.list_candidates("pending") == ()


def test_agent_processor_sends_only_approved_memories_to_agent_core(tmp_path: Path) -> None:
    """Pending, rejected, and deleted candidates never cross the Agent Core boundary."""
    store = MemoryStore(tmp_path / "voice.sqlite3")
    store.migrate()
    agent = FakeAgent()
    processor = InspectingAgentProcessor(agent, memory_store=store)

    async def exercise() -> None:
        await processor.process_frame(
            TranscriptionFrame("第一轮", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )
        conversation_id = processor.conversation_id
        assert conversation_id is not None
        source_message = store.list_messages(conversation_id)[0]
        pending = store.create_candidate(source_message.id, "待确认偏好")
        approved = store.create_candidate(source_message.id, "用户偏好简洁回答")
        rejected = store.create_candidate(source_message.id, "错误事实")
        deleted = store.create_candidate(source_message.id, "已删除事实")
        store.decide_candidate(approved.id, "approved", "administrator")
        store.decide_candidate(rejected.id, "rejected", "administrator")
        store.decide_candidate(deleted.id, "approved", "administrator")
        store.decide_candidate(deleted.id, "deleted", "administrator")
        assert pending.status == "pending"
        await processor.process_frame(
            TranscriptionFrame("第二轮", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )

    asyncio.run(exercise())

    assert agent.requests[1].approved_memories == ("用户偏好简洁回答",)


class FakeRetriever:
    """Record the transcription supplied to synchronous local retrieval."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def retrieve(self, query: str) -> tuple[str, ...]:
        self.queries.append(query)
        return ("来源：manual.md\n本机部署步骤",)


def test_agent_processor_adds_local_retrieval_context_to_only_the_current_request() -> None:
    """RAG lookup runs off the media loop and does not become conversation history."""
    agent = FakeAgent()
    retriever = FakeRetriever()
    processor = InspectingAgentProcessor(agent, retriever=retriever)

    async def exercise() -> None:
        await processor.process_frame(
            TranscriptionFrame("如何本机部署？", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )

    asyncio.run(exercise())

    assert retriever.queries == ["如何本机部署？"]
    assert agent.requests[0].retrieved_context == ("来源：manual.md\n本机部署步骤",)
    assert agent.requests[0].history == ()


class ToolRequestAgent:
    """Yield one tool request so the processor can exercise the policy gateway boundary."""

    async def respond(self, request: AgentRequest) -> AsyncIterator[AgentResponseEvent]:
        yield AgentResponseEvent(
            request.utterance_id,
            AgentResponseKind.TOOL_REQUEST,
            tool_request=ToolRequest("terminal", '{"argv":["pwd"],"cwd":"."}'),
        )
        yield AgentResponseEvent(request.utterance_id, AgentResponseKind.END)

class FakeToolGateway:
    """Return one controlled gateway result without invoking the real terminal adapter."""

    def __init__(self, outcome: ToolOutcome) -> None:
        self.outcome = outcome
        self.requests: list[ToolRequest] = []
        self.cancelled_approval_ids: list[str] = []

    async def dispatch(self, request: ToolRequest) -> ToolOutcome:
        self.requests.append(request)
        return self.outcome

    async def cancel_pending(self, approval_id: str) -> None:
        self.cancelled_approval_ids.append(approval_id)


class ContinuingToolAgent:
    """Request one tool and produce a final answer only after receiving its result."""

    def __init__(self) -> None:
        self.results: list[ToolResult] = []

    async def respond(self, request: AgentRequest) -> AsyncIterator[AgentResponseEvent]:
        yield AgentResponseEvent(
            request.utterance_id,
            AgentResponseKind.TOOL_REQUEST,
            tool_request=ToolRequest(
                "terminal", '{"argv":["pwd"],"cwd":"."}', call_id="call-1"
            ),
        )
        yield AgentResponseEvent(request.utterance_id, AgentResponseKind.END)

    async def respond_to_tool_result(
        self, request: AgentRequest, result: ToolResult
    ) -> AsyncIterator[AgentResponseEvent]:
        self.results.append(result)
        yield AgentResponseEvent(request.utterance_id, AgentResponseKind.TEXT, "当前目录已确认。")
        yield AgentResponseEvent(request.utterance_id, AgentResponseKind.END)


class SilentContinuationAgent(ContinuingToolAgent):
    """Finish a tool continuation without text to exercise the browser-only fallback."""

    async def respond_to_tool_result(
        self, request: AgentRequest, result: ToolResult
    ) -> AsyncIterator[AgentResponseEvent]:
        self.results.append(result)
        yield AgentResponseEvent(request.utterance_id, AgentResponseKind.END)


class BlockingContinuationAgent(ContinuingToolAgent):
    """Hold a post-approval result open until a newer transcription supersedes it."""

    def __init__(self) -> None:
        super().__init__()
        self.continuation_started = asyncio.Event()
        self.continuation_cancelled = asyncio.Event()
        self.requests = 0

    async def respond(self, request: AgentRequest) -> AsyncIterator[AgentResponseEvent]:
        self.requests += 1
        if self.requests == 1:
            async for event in super().respond(request):
                yield event
            return
        yield AgentResponseEvent(request.utterance_id, AgentResponseKind.TEXT, "新回答")
        yield AgentResponseEvent(request.utterance_id, AgentResponseKind.END)

    async def respond_to_tool_result(
        self, request: AgentRequest, result: ToolResult
    ) -> AsyncIterator[AgentResponseEvent]:
        self.results.append(result)
        self.continuation_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.continuation_cancelled.set()
            raise
        yield AgentResponseEvent(request.utterance_id, AgentResponseKind.TEXT, "过期回答")

def test_agent_processor_routes_tool_requests_through_gateway_and_renders_bounded_result(
    tmp_path: Path,
) -> None:
    """The media layer never executes an Agent Core tool request directly."""
    result = TerminalResult(0, "workspace\n", "", False, False)
    gateway = FakeToolGateway(ToolOutcome("completed", "done", result=result))
    store = MemoryStore(tmp_path / "voice.sqlite3")
    store.migrate()
    processor = InspectingAgentProcessor(
        ToolRequestAgent(), memory_store=store, tool_gateway=gateway
    )

    async def exercise() -> None:
        await processor.process_frame(
            TranscriptionFrame("当前目录", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )

    asyncio.run(exercise())

    assert [request.name for request in gateway.requests] == ["terminal"]
    text_frames = [frame.text for frame in processor.forwarded if isinstance(frame, LLMTextFrame)]
    assert text_frames == ["本地操作结果：workspace\n"]
    conversation_id = processor.conversation_id
    assert conversation_id is not None
    messages = store.list_messages(conversation_id)
    persisted = [(message.role, message.content) for message in messages]
    assert persisted == [("user", "当前目录")]


def test_agent_processor_leaves_approval_required_tool_without_side_effect() -> None:
    """Approval-required requests only instruct the user to review them in the browser."""
    gateway = FakeToolGateway(ToolOutcome("approval_required", "review", approval_id="approval-1"))
    processor = InspectingAgentProcessor(ToolRequestAgent(), tool_gateway=gateway)

    async def exercise() -> None:
        await processor.process_frame(
            TranscriptionFrame("创建文件", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )

    asyncio.run(exercise())

    text_frames = [frame.text for frame in processor.forwarded if isinstance(frame, LLMTextFrame)]
    assert text_frames == ["此本地操作已提交，请在浏览器中批准。"]


def test_agent_processor_continues_once_after_browser_approval(tmp_path: Path) -> None:
    """A consumed active-session approval resumes through one untrusted-result model turn."""
    gateway = FakeToolGateway(ToolOutcome("approval_required", "review", approval_id="approval-1"))
    agent = ContinuingToolAgent()
    store = MemoryStore(tmp_path / "voice.sqlite3")
    store.migrate()
    processor = InspectingAgentProcessor(agent, memory_store=store, tool_gateway=gateway)
    result = TerminalResult(0, "created\n", "", False, False)

    async def exercise() -> bool:
        await processor.process_frame(
            TranscriptionFrame("创建文件", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )
        continued = await processor.continue_after_approved_tool(
            "approval-1", ToolOutcome("completed", "done", result=result)
        )
        response_task = processor._response_task
        assert response_task is not None
        await response_task
        return continued

    assert asyncio.run(exercise())
    assert [tool_result.request.call_id for tool_result in agent.results] == ["call-1"]
    assert "created" in agent.results[0].content
    text_frames = [frame.text for frame in processor.forwarded if isinstance(frame, LLMTextFrame)]
    assert text_frames == ["此本地操作已提交，请在浏览器中批准。", "当前目录已确认。"]
    conversation_id = processor.conversation_id
    assert conversation_id is not None
    messages = store.list_messages(conversation_id)
    assert [(message.role, message.content) for message in messages] == [
        ("user", "创建文件"),
        ("assistant", "当前目录已确认。"),
    ]


def test_agent_processor_never_persists_tool_output_when_continuation_has_no_text(
    tmp_path: Path,
) -> None:
    """A browser-visible fallback remains outside assistant history when the model emits no text."""
    gateway = FakeToolGateway(ToolOutcome("approval_required", "review", approval_id="approval-1"))
    store = MemoryStore(tmp_path / "voice.sqlite3")
    store.migrate()
    processor = InspectingAgentProcessor(
        SilentContinuationAgent(), memory_store=store, tool_gateway=gateway
    )
    result = TerminalResult(0, "untrusted-output\n", "", False, False)

    async def exercise() -> None:
        await processor.process_frame(
            TranscriptionFrame("创建文件", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )
        assert await processor.continue_after_approved_tool(
            "approval-1", ToolOutcome("completed", "done", result=result)
        )
        response_task = processor._response_task
        assert response_task is not None
        await response_task

    asyncio.run(exercise())
    text_frames = [frame.text for frame in processor.forwarded if isinstance(frame, LLMTextFrame)]
    assert text_frames[-1] == "本地操作结果：untrusted-output\n"
    conversation_id = processor.conversation_id
    assert conversation_id is not None
    messages = store.list_messages(conversation_id)
    assert [(message.role, message.content) for message in messages] == [("user", "创建文件")]


def test_new_transcription_cancels_an_inflight_browser_approval_continuation() -> None:
    """A later utterance owns output after it invalidates the old approval generation."""
    gateway = FakeToolGateway(ToolOutcome("approval_required", "review", approval_id="approval-1"))
    agent = BlockingContinuationAgent()
    processor = InspectingAgentProcessor(agent, tool_gateway=gateway)

    async def exercise() -> None:
        await processor.process_frame(
            TranscriptionFrame("创建文件", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )
        assert await processor.continue_after_approved_tool(
            "approval-1", ToolOutcome("completed", "done")
        )
        old_task = processor._response_task
        assert old_task is not None
        await agent.continuation_started.wait()
        await processor._respond_to_transcription(
            TranscriptionFrame("下一件事", "user", "later")
        )
        try:
            await old_task
        except asyncio.CancelledError:
            pass

    asyncio.run(exercise())
    assert agent.continuation_cancelled.is_set()
    text_frames = [frame.text for frame in processor.forwarded if isinstance(frame, LLMTextFrame)]
    assert "过期回答" not in text_frames
    assert text_frames[-1] == "新回答"


def test_agent_processor_revokes_pending_approval_on_interruption() -> None:
    """An interrupted browser approval cannot later resume its superseded answer."""
    gateway = FakeToolGateway(ToolOutcome("approval_required", "review", approval_id="approval-1"))
    processor = InspectingAgentProcessor(ContinuingToolAgent(), tool_gateway=gateway)

    async def exercise() -> bool:
        await processor.process_frame(
            TranscriptionFrame("创建文件", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )
        await processor._cancel_pending_tool_continuation()
        return await processor.continue_after_approved_tool(
            "approval-1", ToolOutcome("completed", "done")
        )

    assert not asyncio.run(exercise())
    assert gateway.cancelled_approval_ids == ["approval-1"]


def test_agent_processor_continues_a_completed_tool_with_untrusted_result_data(
    tmp_path: Path,
) -> None:
    """Only the model's follow-up answer becomes assistant history, never raw terminal output."""
    result = TerminalResult(0, "workspace\n", "", False, False)
    gateway = FakeToolGateway(ToolOutcome("completed", "done", result=result))
    agent = ContinuingToolAgent()
    store = MemoryStore(tmp_path / "voice.sqlite3")
    store.migrate()
    processor = InspectingAgentProcessor(agent, memory_store=store, tool_gateway=gateway)

    async def exercise() -> None:
        await processor.process_frame(
            TranscriptionFrame("当前目录", "user", "timestamp"), FrameDirection.DOWNSTREAM
        )

    asyncio.run(exercise())

    assert [result.request.call_id for result in agent.results] == ["call-1"]
    assert "exit_code=0" in agent.results[0].content
    assert "workspace" in agent.results[0].content
    text_frames = [frame.text for frame in processor.forwarded if isinstance(frame, LLMTextFrame)]
    assert text_frames == ["当前目录已确认。"]
    conversation_id = processor.conversation_id
    assert conversation_id is not None
    messages = store.list_messages(conversation_id)
    persisted = [(message.role, message.content) for message in messages]
    assert persisted == [("user", "当前目录"), ("assistant", "当前目录已确认。")]


class BlockingToolGateway:
    """Block dispatch until the interruption path cancels its response task."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def dispatch(self, request: ToolRequest) -> ToolOutcome:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("blocking dispatch should have been cancelled")

    async def cancel_pending(self, approval_id: str) -> None:
        """Satisfy the complete gateway contract; this fake never creates an approval."""
        raise AssertionError(f"unexpected pending approval cancellation: {approval_id}")


class DelayedCancellationGateway:
    """Model a gateway that must finish approval creation cleanup after response cancellation."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.release_cleanup = asyncio.Event()

    async def dispatch(self, request: ToolRequest) -> ToolOutcome:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cleanup_started.set()
            await self.release_cleanup.wait()
            raise
        raise AssertionError(f"unexpected completed dispatch: {request}")

    async def cancel_pending(self, approval_id: str) -> None:
        """Satisfy the complete gateway contract; no approval record has been exposed yet."""
        raise AssertionError(f"unexpected pending approval cancellation: {approval_id}")


def test_agent_processor_cancels_an_active_tool_dispatch_on_interruption() -> None:
    """A barge-in cannot leave a pending media response executing its tool request."""
    gateway = BlockingToolGateway()
    processor = InspectingAgentProcessor(ToolRequestAgent(), tool_gateway=gateway)

    async def exercise() -> None:
        response_task = asyncio.create_task(
            processor.process_frame(
                TranscriptionFrame("执行操作", "user", "timestamp"), FrameDirection.DOWNSTREAM
            )
        )
        await gateway.started.wait()
        processor._cancel_active_response()
        await gateway.cancelled.wait()
        try:
            await response_task
        except asyncio.CancelledError:
            pass

    asyncio.run(exercise())

    assert not any(isinstance(frame, LLMFullResponseEndFrame) for frame in processor.forwarded)


def test_abandon_waits_for_cancelled_approval_creation_cleanup() -> None:
    """Session shutdown waits for cancelled dispatch cleanup before releasing ownership."""
    gateway = DelayedCancellationGateway()
    processor = InspectingAgentProcessor(ToolRequestAgent(), tool_gateway=gateway)

    async def exercise() -> None:
        response_task = asyncio.create_task(
            processor._respond_to_transcription(
                TranscriptionFrame("创建文件", "user", "timestamp")
            )
        )
        await gateway.started.wait()
        abandon_task = asyncio.create_task(processor.abandon_pending_tool_continuation())
        await gateway.cleanup_started.wait()
        assert not abandon_task.done()
        gateway.release_cleanup.set()
        await abandon_task
        try:
            await response_task
        except asyncio.CancelledError:
            pass

    asyncio.run(exercise())
