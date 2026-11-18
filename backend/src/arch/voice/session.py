"""Ownership and cleanup for one authenticated local voice media session."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import WebSocket
from pipecat.pipeline.worker import PipelineWorker
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.workers.runner import WorkerRunner

from arch.avatar.adapter import AvatarAdapter
from arch.conversation.profiles import (
    ActiveGreetingPolicy,
    ConversationProfile,
    ConversationProfileStore,
)
from arch.memory.store import MemoryStore
from arch.rag.embeddings import SentenceTransformerEmbedder
from arch.rag.retriever import LocalKnowledgeRetriever, Retriever
from arch.rag.store import KnowledgeStore
from arch.tools.gateway import ToolGateway, ToolOutcome
from arch.voice.agent_processor import PipecatAgentProcessor
from arch.voice.llama import LlamaCppServer
from arch.voice.models import VoiceModelPaths
from arch.voice.pipeline import (
    create_pipeline_worker,
    create_websocket_pipeline_worker,
)
from arch.voice.profiles import VoiceProfile, VoiceProfileStore
from arch.voice.qwen_clone import QwenCloneRuntime


class LocalVoiceSession:
    """Own local model services across media reconnects and one active Pipecat worker."""

    def __init__(
        self,
        paths: VoiceModelPaths,
        memory_store: MemoryStore | None = None,
        *,
        database_path: Path | None = None,
        rag_model_path: Path | None = None,
        tool_gateway: ToolGateway | None = None,
        voice_profile_store: VoiceProfileStore | None = None,
        conversation_profile_store: ConversationProfileStore | None = None,
        data_dir: Path | None = None,
        avatar_observer: AvatarAdapter | None = None,
        avatar_observer_factory: Callable[[str], AvatarAdapter] | None = None,
    ) -> None:
        self._paths = paths
        self._memory_store = memory_store
        self._database_path = database_path
        self._rag_model_path = rag_model_path
        self._tool_gateway = tool_gateway
        self._voice_profile_store = voice_profile_store
        self._conversation_profile_store = conversation_profile_store
        self._data_dir = data_dir
        self._avatar_observer = avatar_observer
        self._avatar_observer_factory = avatar_observer_factory
        self._clone_runtime: QwenCloneRuntime | None = None
        self._retriever: Retriever | None = None
        self._llama = LlamaCppServer(paths)
        self._lifecycle_lock = asyncio.Lock()
        self._runner: WorkerRunner | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._runner_session_id: str | None = None
        self._agent_processor: PipecatAgentProcessor | None = None

    async def start(self, connection: SmallWebRTCConnection) -> None:
        """Start model serving and the media worker exactly once for the active peer."""
        async with self._lifecycle_lock:
            if self._runner_task is not None:
                return
            runner: WorkerRunner | None = None
            runner_task: asyncio.Task[None] | None = None
            try:
                profile = await self._start_selected_clone_runtime()
                conversation_profile = self._selected_conversation_profile()
                await self._llama.start()
                self._retriever = await asyncio.to_thread(self._create_retriever)
                session_id = getattr(connection, "_arch_session_id", None)
                worker = self._create_webrtc_worker(
                    connection,
                    self._paths,
                    self._llama.endpoint,
                    self._memory_store,
                    self._retriever,
                    self._tool_gateway,
                    profile,
                    self._clone_runtime.synthesize if self._clone_runtime is not None else None,
                    self._clone_runtime.cancel if self._clone_runtime is not None else None,
                    session_id,
                    conversation_profile,
                )
                runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
                ready = asyncio.Event()

                @runner.event_handler("on_ready")  # type: ignore[untyped-decorator]
                async def mark_ready(_: WorkerRunner) -> None:
                    ready.set()

                await runner.add_workers(worker)
                runner_task = asyncio.create_task(runner.run(auto_end=False))
                await asyncio.wait_for(ready.wait(), timeout=20)
                self._runner = runner
                self._runner_task = runner_task
                self._runner_session_id = session_id
                if self._agent_processor is not None:
                    self._agent_processor.arm_active_greeting()
                connection.send_app_message({"type": "voice_ready"})
            except BaseException:
                self._agent_processor = None
                if runner is not None:
                    await runner.cancel(reason="voice session startup failed")
                if runner_task is not None:
                    await runner_task
                await self._llama.stop()
                await self._stop_clone_runtime()
                raise

    async def start_websocket(self, websocket: WebSocket, session_id: str | None = None) -> None:
        """Start the sole local media worker using a same-origin PCM WebSocket."""
        async with self._lifecycle_lock:
            if self._runner_task is not None:
                if session_id is None or self._runner_session_id != session_id:
                    raise RuntimeError("Voice session is in use.")
                await self._stop_locked()

            def send_event(event: dict[str, str | int]) -> None:
                asyncio.create_task(websocket.send_text(json.dumps(event, separators=(",", ":"))))

            runner: WorkerRunner | None = None
            runner_task: asyncio.Task[None] | None = None
            session_started = False
            try:
                profile = await self._start_selected_clone_runtime()
                conversation_profile = self._selected_conversation_profile()
                await self._llama.start()
                self._retriever = await asyncio.to_thread(self._create_retriever)
                observer = self._avatar_observer_for_session(session_id)
                worker = create_websocket_pipeline_worker(
                    websocket,
                    send_event,
                    self._paths,
                    self._llama.endpoint,
                    self._memory_store,
                    self._retriever,
                    self._tool_gateway,
                    self._set_agent_processor,
                    profile,
                    self._clone_runtime.synthesize if self._clone_runtime is not None else None,
                    self._clone_runtime.cancel if self._clone_runtime is not None else None,
                    session_id=session_id,
                    avatar_observer=observer,
                    conversation_profile=conversation_profile,
                    active_greeting_policy=self._selected_active_greeting_policy(
                        conversation_profile
                    ),
                )
                runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
                ready = asyncio.Event()

                @runner.event_handler("on_ready")  # type: ignore[untyped-decorator]
                async def mark_ready(_: WorkerRunner) -> None:
                    ready.set()

                await runner.add_workers(worker)
                runner_task = asyncio.create_task(runner.run(auto_end=False))
                await asyncio.wait_for(ready.wait(), timeout=20)
                self._runner = runner
                self._runner_task = runner_task
                self._runner_session_id = session_id
                session_started = True
                if self._agent_processor is not None:
                    self._agent_processor.arm_active_greeting()
                send_event({"type": "voice_ready"})
            except BaseException:
                self._agent_processor = None
                if runner is not None:
                    await runner.cancel(reason="voice session startup failed")
                if runner_task is not None:
                    await runner_task
                await self._llama.stop()
                await self._stop_clone_runtime()
                raise

        try:
            assert runner_task is not None
            await runner_task
        finally:
            async with self._lifecycle_lock:
                if self._runner is runner:
                    await self._stop_locked()
                elif runner is not None:
                    await runner.cancel(reason="voice session closed")
                if not session_started:
                    await self._llama.stop()
                    await self._stop_clone_runtime()

    async def continue_after_approved_tool(self, approval_id: str, outcome: ToolOutcome) -> bool:
        """Resume only the active media session that originated a consumed browser approval."""
        processor = self._agent_processor
        if processor is None:
            return False
        return await processor.continue_after_approved_tool(approval_id, outcome)

    def _set_agent_processor(self, processor: PipecatAgentProcessor) -> None:
        """Expose only the continuation boundary, not the full media pipeline, to this session."""
        self._agent_processor = processor

    def set_avatar_observer(self, observer: AvatarAdapter | None) -> None:
        """Attach or detach the optional video observer before the next pipeline starts."""
        self._avatar_observer = observer

    def set_avatar_observer_factory(self, factory: Callable[[str], AvatarAdapter] | None) -> None:
        """Create a session-bound media observer only for an authenticated voice peer."""
        self._avatar_observer_factory = factory

    def _create_webrtc_worker(
        self,
        connection: SmallWebRTCConnection,
        paths: VoiceModelPaths,
        llama_endpoint: str,
        memory_store: MemoryStore | None,
        retriever: Retriever | None,
        tool_gateway: ToolGateway | None,
        profile: VoiceProfile | None,
        clone_synthesize: Callable[[str, VoiceProfile], Awaitable[tuple[bytes, int]]] | None,
        clone_cancel: Callable[[], Awaitable[None]] | None,
        session_id: str | None,
        conversation_profile: ConversationProfile | None,
    ) -> PipelineWorker:
        """Pass the optional observer without changing test-factory call shapes."""
        arguments = (
            connection,
            paths,
            llama_endpoint,
            memory_store,
            retriever,
            tool_gateway,
            self._set_agent_processor,
            profile,
            clone_synthesize,
            clone_cancel,
        )
        observer = self._avatar_observer_for_session(session_id)
        active_greeting_policy = self._selected_active_greeting_policy(conversation_profile)
        if conversation_profile is not None:
            return create_pipeline_worker(
                *arguments,
                session_id=session_id,
                avatar_observer=observer,
                conversation_profile=conversation_profile,
                active_greeting_policy=active_greeting_policy,
            )
        if observer is None and session_id is None:
            return create_pipeline_worker(*arguments)
        if observer is None:
            return create_pipeline_worker(*arguments, session_id=session_id)
        if session_id is None:
            return create_pipeline_worker(*arguments, avatar_observer=observer)
        return create_pipeline_worker(
            *arguments,
            session_id=session_id,
            avatar_observer=observer,
        )

    def _avatar_observer_for_session(self, session_id: str | None) -> AvatarAdapter | None:
        """Create the authenticated browser's observer for either voice transport."""
        if session_id is not None and self._avatar_observer_factory is not None:
            return self._avatar_observer_factory(session_id)
        return self._avatar_observer

    async def _start_selected_clone_runtime(self) -> VoiceProfile | None:
        """Start or reuse Qwen for a currently selected, locally valid clone profile."""
        profile = self._selected_clone_profile()
        if profile is None:
            await self._stop_clone_runtime()
            return None
        if self._data_dir is None:
            raise RuntimeError("Selected Qwen clone profile has no local data directory.")
        runtime = self._clone_runtime
        if runtime is None:
            runtime = QwenCloneRuntime(self._data_dir)
            self._clone_runtime = runtime
        await runtime.start()
        return profile

    def _selected_clone_profile(self) -> VoiceProfile | None:
        """Return the selected profile only when its metadata still exists locally."""
        store = self._voice_profile_store
        if store is None:
            return None
        profile_id = store.selected_id()
        return store.get(profile_id) if profile_id is not None else None

    def _selected_conversation_profile(self) -> ConversationProfile | None:
        """Snapshot the selected style profile once, before a new media worker starts."""
        store = self._conversation_profile_store
        return store.selected() if store is not None else None

    def _selected_active_greeting_policy(
        self, profile: ConversationProfile | None
    ) -> ActiveGreetingPolicy | None:
        store = self._conversation_profile_store
        if profile is None or store is None:
            return None
        getter = getattr(store, "active_greeting_policy", None)
        return getter(profile.id) if callable(getter) else None

    async def _stop_clone_runtime(self) -> None:
        """Stop and forget the session-owned Qwen worker."""
        runtime, self._clone_runtime = self._clone_runtime, None
        if runtime is not None:
            await runtime.stop()

    def _create_retriever(self) -> Retriever | None:
        """Load the configured offline embedding model only when a voice session starts."""
        if self._database_path is None and self._rag_model_path is None:
            return None
        if self._database_path is None or self._rag_model_path is None:
            raise RuntimeError("Local RAG configuration is incomplete.")
        if not self._rag_model_path.is_dir():
            raise RuntimeError("Local RAG model is not ready; run make rag-prepare first.")
        return LocalKnowledgeRetriever(
            KnowledgeStore(self._database_path),
            SentenceTransformerEmbedder(str(self._rag_model_path)),
        )

    async def stop(self) -> None:
        """Cancel media work while retaining an idle Qwen worker for profile reconnects."""
        async with self._lifecycle_lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        """Stop the active worker while the lifecycle lock is held."""
        runner, task = self._runner, self._runner_task
        processor = self._agent_processor
        self._runner = None
        self._runner_task = None
        self._runner_session_id = None
        self._retriever = None
        if processor is not None:
            await processor.abandon_pending_tool_continuation()
        self._agent_processor = None
        if runner is not None:
            await runner.cancel(reason="voice session closed")
        if task is not None and task is not asyncio.current_task():
            await task
        if self._selected_clone_profile() is None:
            await self._stop_clone_runtime()

    async def close(self) -> None:
        """Stop the media session and release all session-owned model workers."""
        async with self._lifecycle_lock:
            await self._stop_locked()
            await self._stop_clone_runtime()
            await self._llama.stop()
