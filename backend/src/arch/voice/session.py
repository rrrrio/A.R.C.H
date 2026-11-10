"""Ownership and cleanup for one authenticated local voice media session."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import WebSocket
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.workers.runner import WorkerRunner

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
    """Own the one local model server and Pipecat worker attached to a peer connection."""

    def __init__(
        self,
        paths: VoiceModelPaths,
        memory_store: MemoryStore | None = None,
        *,
        database_path: Path | None = None,
        rag_model_path: Path | None = None,
        tool_gateway: ToolGateway | None = None,
        voice_profile_store: VoiceProfileStore | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self._paths = paths
        self._memory_store = memory_store
        self._database_path = database_path
        self._rag_model_path = rag_model_path
        self._tool_gateway = tool_gateway
        self._voice_profile_store = voice_profile_store
        self._data_dir = data_dir
        self._clone_runtime: QwenCloneRuntime | None = None
        self._retriever: Retriever | None = None
        self._llama = LlamaCppServer(paths)
        self._runner: WorkerRunner | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._agent_processor: PipecatAgentProcessor | None = None

    async def start(self, connection: SmallWebRTCConnection) -> None:
        """Start model serving and the media worker exactly once for the active peer."""
        if self._runner_task is not None:
            return
        profile = await self._start_selected_clone_runtime()
        await self._llama.start()
        runner: WorkerRunner | None = None
        runner_task: asyncio.Task[None] | None = None
        try:
            self._retriever = await asyncio.to_thread(self._create_retriever)
            worker = create_pipeline_worker(
                connection,
                self._paths,
                self._llama.endpoint,
                self._memory_store,
                self._retriever,
                self._tool_gateway,
                self._set_agent_processor,
                profile,
                self._clone_runtime.synthesize if self._clone_runtime is not None else None,
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

    async def start_websocket(self, websocket: WebSocket) -> None:
        """Start the sole local media worker using a same-origin PCM WebSocket."""
        if self._runner_task is not None:
            raise RuntimeError("Voice session is in use.")

        def send_event(event: dict[str, str | int]) -> None:
            asyncio.create_task(websocket.send_text(json.dumps(event, separators=(",", ":"))))

        profile = await self._start_selected_clone_runtime()
        await self._llama.start()
        runner: WorkerRunner | None = None
        runner_task: asyncio.Task[None] | None = None
        try:
            self._retriever = await asyncio.to_thread(self._create_retriever)
            worker = create_websocket_pipeline_worker(
                websocket, send_event, self._paths, self._llama.endpoint, self._memory_store,
                self._retriever,
                self._tool_gateway,
                self._set_agent_processor,
                profile,
                self._clone_runtime.synthesize if self._clone_runtime is not None else None,
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
            send_event({"type": "voice_ready"})
            await runner_task
        finally:
            if self._runner is runner:
                await self.stop()
            elif runner is not None:
                await runner.cancel(reason="voice session closed")
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

    async def _start_selected_clone_runtime(self) -> VoiceProfile | None:
        """Start Qwen only for a currently selected, locally valid clone profile."""
        store = self._voice_profile_store
        if store is None:
            return None
        profile_id = store.selected_id()
        profile = store.get(profile_id) if profile_id is not None else None
        if profile is None:
            return None
        if self._data_dir is None:
            raise RuntimeError("Selected Qwen clone profile has no local data directory.")
        runtime = QwenCloneRuntime(self._data_dir)
        await runtime.start()
        self._clone_runtime = runtime
        return profile

    async def _stop_clone_runtime(self) -> None:
        """Stop selected-profile Qwen synthesis before allowing a later session to start."""
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
        """Cancel media work before stopping the local language-model subprocess."""
        runner, task = self._runner, self._runner_task
        processor = self._agent_processor
        self._runner = None
        self._runner_task = None
        self._retriever = None
        if processor is not None:
            await processor.abandon_pending_tool_continuation()
        self._agent_processor = None
        await self._stop_clone_runtime()
        if runner is not None:
            await runner.cancel(reason="voice session closed")
        if task is not None and task is not asyncio.current_task():
            await task
        await self._llama.stop()
