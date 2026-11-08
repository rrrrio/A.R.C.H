"""Ownership and cleanup for one authenticated local voice media session."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.workers.runner import WorkerRunner

from local_voice_agent.memory.store import MemoryStore
from local_voice_agent.rag.embeddings import SentenceTransformerEmbedder
from local_voice_agent.rag.retriever import LocalKnowledgeRetriever, Retriever
from local_voice_agent.rag.store import KnowledgeStore
from local_voice_agent.voice.llama import LlamaCppServer
from local_voice_agent.voice.models import VoiceModelPaths
from local_voice_agent.voice.pipeline import create_pipeline_worker


class LocalVoiceSession:
    """Own the one local model server and Pipecat worker attached to a peer connection."""

    def __init__(
        self,
        paths: VoiceModelPaths,
        memory_store: MemoryStore | None = None,
        *,
        database_path: Path | None = None,
        rag_model_path: Path | None = None,
    ) -> None:
        self._paths = paths
        self._memory_store = memory_store
        self._database_path = database_path
        self._rag_model_path = rag_model_path
        self._retriever: Retriever | None = None
        self._llama = LlamaCppServer(paths)
        self._runner: WorkerRunner | None = None
        self._runner_task: asyncio.Task[None] | None = None

    async def start(self, connection: SmallWebRTCConnection) -> None:
        """Start model serving and the media worker exactly once for the active peer."""
        if self._runner_task is not None:
            return
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
        except BaseException:
            if runner is not None:
                await runner.cancel(reason="voice session startup failed")
            if runner_task is not None:
                await runner_task
            await self._llama.stop()
            raise

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
        self._runner = None
        self._runner_task = None
        self._retriever = None
        if runner is not None:
            await runner.cancel(reason="voice session closed")
        if task is not None:
            await task
        await self._llama.stop()
