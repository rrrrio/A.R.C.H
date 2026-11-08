"""Translate finalized media transcription into Agent Core response frames."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol

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
    ToolRequest,
    ToolResult,
    ToolResultAgentCore,
)
from local_voice_agent.memory.context import bounded_approved_memories
from local_voice_agent.memory.extractor import MemoryCandidateExtractor
from local_voice_agent.memory.store import MemoryStore, StoredMessage
from local_voice_agent.rag.retriever import Retriever
from local_voice_agent.tools.gateway import ToolOutcome


class ToolDispatcher(Protocol):
    """Minimal tool boundary needed by the media-facing processor."""

    async def dispatch(self, request: ToolRequest) -> ToolOutcome:
        """Classify and execute only tool requests admitted by application policy."""

    async def cancel_pending(self, approval_id: str) -> None:
        """Revoke a browser approval whose originating media turn was abandoned."""


@dataclass(frozen=True, slots=True)
class _PendingToolContinuation:
    """Keep one active-session-only callback context for an unconsumed approval."""

    approval_id: str
    request: AgentRequest
    tool_request: ToolRequest
    stored_user: StoredMessage | None
    language: str


class PipecatAgentProcessor(FrameProcessor):
    """Keep media framing in Pipecat while the Agent Core owns local text generation."""

    def __init__(
        self,
        agent: AgentCore,
        history_limit: int = 20,
        memory_store: MemoryStore | None = None,
        candidate_extractor: MemoryCandidateExtractor | None = None,
        retriever: Retriever | None = None,
        tool_gateway: ToolDispatcher | None = None,
    ) -> None:
        super().__init__()
        self._agent = agent
        self._history_limit = history_limit
        self._history: list[ConversationTurn] = []
        self._active_utterance_id: str | None = None
        self._memory_store = memory_store
        self._candidate_extractor = candidate_extractor
        self._retriever = retriever
        self._tool_gateway = tool_gateway
        self._candidate_task: asyncio.Task[None] | None = None
        self._response_task: asyncio.Task[object] | None = None
        self._pending_tool_continuation: _PendingToolContinuation | None = None
        self._turn_generation = 0
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
            self._begin_new_turn()
            await self._cancel_pending_tool_continuation()
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, TranscriptionFrame):
            self._cancel_candidate_extraction()
            await self._respond_to_transcription(frame)
            return
        await self.push_frame(frame, direction)

    async def _respond_to_transcription(self, frame: TranscriptionFrame) -> None:
        generation = self._begin_new_turn()
        await self._cancel_pending_tool_continuation()
        current_task = asyncio.current_task()
        if current_task is not None:
            self._response_task = current_task
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
        try:
            if not self._is_current_turn(utterance_id, generation):
                return
            stored_user = await self._record_turn(
                ConversationTurn("user", frame.text, utterance_id)
            )
            assistant_history_parts: list[str] = []
            await self.push_frame(LLMFullResponseStartFrame())
            async for event in self._agent.respond(request):
                if event.utterance_id != utterance_id or not self._is_current_turn(
                    utterance_id, generation
                ):
                    continue
                if event.kind in {AgentResponseKind.TEXT, AgentResponseKind.ERROR}:
                    assistant_history_parts.append(event.text)
                    await self.push_frame(LLMTextFrame(event.text))
                elif event.kind == AgentResponseKind.TOOL_REQUEST:
                    outcome = await self._dispatch_tool_request(event.tool_request)
                    if outcome is None:
                        continue
                    if outcome.status == "approval_required":
                        self._remember_pending_tool_continuation(
                            outcome, request, event.tool_request, stored_user, language
                        )
                    continued = await self._continue_after_completed_tool(
                        request, event.tool_request, outcome, assistant_history_parts, generation
                    )
                    if not continued:
                        tool_text = _tool_outcome_text(outcome)
                        if tool_text:
                            await self.push_frame(LLMTextFrame(tool_text))
                elif event.kind == AgentResponseKind.END:
                    break
            if not self._is_current_turn(utterance_id, generation):
                return
            assistant_text = "".join(assistant_history_parts)
            if assistant_text:
                await self._record_turn(ConversationTurn("assistant", assistant_text, utterance_id))
                self._start_candidate_extraction(stored_user, language)
            self._active_utterance_id = None
            await self.push_frame(LLMFullResponseEndFrame())
        finally:
            if self._response_task is current_task:
                self._response_task = None

    async def continue_after_approved_tool(self, approval_id: str, outcome: ToolOutcome) -> bool:
        """Schedule one final answer only for the still-active originating browser approval."""
        pending = self._pending_tool_continuation
        self._pending_tool_continuation = None
        if pending is None or pending.approval_id != approval_id or outcome.status != "completed":
            return False
        if self._active_utterance_id is not None or not isinstance(
            self._agent, ToolResultAgentCore
        ):
            return False
        generation = self._turn_generation
        self._active_utterance_id = pending.request.utterance_id
        self._response_task = asyncio.create_task(
            self._complete_approved_tool(pending, outcome, generation)
        )
        return True

    async def _complete_approved_tool(
        self,
        pending: _PendingToolContinuation,
        outcome: ToolOutcome,
        generation: int,
    ) -> None:
        """Run a cancellable media task independent of the browser request lifetime."""
        current_task = asyncio.current_task()
        assistant_history_parts: list[str] = []
        try:
            if not self._is_current_turn(pending.request.utterance_id, generation):
                return
            await self.push_frame(LLMFullResponseStartFrame())
            continued = await self._continue_after_completed_tool(
                pending.request,
                pending.tool_request,
                outcome,
                assistant_history_parts,
                generation,
            )
            if not continued and self._is_current_turn(pending.request.utterance_id, generation):
                tool_text = _tool_outcome_text(outcome)
                if tool_text:
                    await self.push_frame(LLMTextFrame(tool_text))
            if not self._is_current_turn(pending.request.utterance_id, generation):
                return
            assistant_text = "".join(assistant_history_parts)
            if assistant_text:
                await self._record_turn(
                    ConversationTurn("assistant", assistant_text, pending.request.utterance_id)
                )
                self._start_candidate_extraction(pending.stored_user, pending.language)
            return
        finally:
            if self._is_current_turn(pending.request.utterance_id, generation):
                self._active_utterance_id = None
                await self.push_frame(LLMFullResponseEndFrame())
            if self._response_task is current_task:
                self._response_task = None

    def _begin_new_turn(self) -> int:
        """Invalidate and cancel any response before a newer media event can take ownership."""
        self._turn_generation += 1
        self._cancel_active_response()
        self._active_utterance_id = None
        return self._turn_generation

    def _is_current_turn(self, utterance_id: str, generation: int) -> bool:
        """Require both stable ownership and generation identity across asynchronous boundaries."""
        return self._active_utterance_id == utterance_id and self._turn_generation == generation

    def _remember_pending_tool_continuation(
        self,
        outcome: ToolOutcome,
        request: AgentRequest,
        tool_request: ToolRequest | None,
        stored_user: StoredMessage | None,
        language: str,
    ) -> None:
        """Retain only a model-issued, call-ID-bound approval until the session changes state."""
        if outcome.approval_id is None or tool_request is None or tool_request.call_id is None:
            return
        self._pending_tool_continuation = _PendingToolContinuation(
            outcome.approval_id, request, tool_request, stored_user, language
        )

    async def _cancel_pending_tool_continuation(self) -> None:
        """Drop and revoke the prior approval before a newer turn can supersede it."""
        pending = self._pending_tool_continuation
        self._pending_tool_continuation = None
        if pending is not None and self._tool_gateway is not None:
            await self._tool_gateway.cancel_pending(pending.approval_id)

    async def abandon_pending_tool_continuation(self) -> None:
        """Invalidate output and revoke an unconsumed approval before its session is released."""
        response_task = self._response_task
        self._begin_new_turn()
        await self._cancel_pending_tool_continuation()
        if response_task is not None and response_task is not asyncio.current_task():
            try:
                await response_task
            except asyncio.CancelledError:
                pass

    async def _dispatch_tool_request(self, request: ToolRequest | None) -> ToolOutcome | None:
        """Send only typed requests through the application policy boundary."""
        if self._tool_gateway is None:
            await self.push_frame(LLMTextFrame("本地工具暂不可用。"))
            return None
        if request is None:
            await self.push_frame(LLMTextFrame("工具请求无效，未执行任何操作。"))
            return None
        return await self._tool_gateway.dispatch(request)

    async def _continue_after_completed_tool(
        self,
        request: AgentRequest,
        tool_request: ToolRequest | None,
        outcome: ToolOutcome,
        assistant_history_parts: list[str],
        generation: int,
    ) -> bool:
        """Let capable Agent Cores summarize one automatic result without exposing it as history."""
        if (
            outcome.status != "completed"
            or tool_request is None
            or tool_request.call_id is None
            or not isinstance(self._agent, ToolResultAgentCore)
        ):
            return False
        result = ToolResult(tool_request, outcome.status, _tool_result_for_agent(outcome))
        emitted = False
        async for event in self._agent.respond_to_tool_result(request, result):
            if event.utterance_id != request.utterance_id or not self._is_current_turn(
                request.utterance_id, generation
            ):
                continue
            if event.kind in {AgentResponseKind.TEXT, AgentResponseKind.ERROR} and event.text:
                emitted = True
                assistant_history_parts.append(event.text)
                await self.push_frame(LLMTextFrame(event.text))
            elif event.kind == AgentResponseKind.END:
                break
        return emitted

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

    def _cancel_active_response(self) -> None:
        """Stop a concurrent response, including a bounded tool invocation, on interruption."""
        task = self._response_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()

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
        await self.abandon_pending_tool_continuation()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await super().cleanup()  # type: ignore[no-untyped-call]


def _tool_outcome_text(outcome: ToolOutcome) -> str:
    """Render bounded tool feedback without placing untrusted output in model history."""
    if outcome.status == "approval_required":
        return "此本地操作已提交，请在浏览器中批准。"
    if outcome.status in {"denied", "unavailable"}:
        return f"本地操作未执行：{outcome.message}"
    if outcome.search_results:
        references = "\n".join(f"{item.title}：{item.url}" for item in outcome.search_results)
        return f"联网搜索结果：\n{references}"
    if outcome.result is None:
        return "本地操作已完成。"
    output = outcome.result.stdout or outcome.result.stderr
    if not output:
        return "本地操作已完成。"
    bounded_output = output[:1000]
    suffix = (
        "（输出已截断）"
        if len(output) > len(bounded_output) or outcome.result.output_truncated
        else ""
    )
    return f"本地操作结果：{bounded_output}{suffix}"


def _tool_result_for_agent(outcome: ToolOutcome) -> str:
    """Describe a completed command in bounded data for the continuation-only Agent Core call."""
    if outcome.search_results:
        return json.dumps(
            [
                {"title": item.title, "url": item.url, "snippet": item.snippet}
                for item in outcome.search_results
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    if outcome.result is None:
        return outcome.message
    output = outcome.result.stdout or outcome.result.stderr
    return (
        f"exit_code={outcome.result.exit_code}; timed_out={outcome.result.timed_out}; "
        f"output_truncated={outcome.result.output_truncated}\n{output}"
    )
