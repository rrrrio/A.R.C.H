"""Ownership and cleanup for one authenticated local voice media session."""

from __future__ import annotations

import asyncio

from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.workers.runner import WorkerRunner

from local_voice_agent.voice.llama import LlamaCppServer
from local_voice_agent.voice.models import VoiceModelPaths
from local_voice_agent.voice.pipeline import create_pipeline_worker


class LocalVoiceSession:
    """Own the one local model server and Pipecat worker attached to a peer connection."""

    def __init__(self, paths: VoiceModelPaths) -> None:
        self._paths = paths
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
            worker = create_pipeline_worker(connection, self._paths, self._llama.endpoint)
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

    async def stop(self) -> None:
        """Cancel media work before stopping the local language-model subprocess."""
        runner, task = self._runner, self._runner_task
        self._runner = None
        self._runner_task = None
        if runner is not None:
            await runner.cancel(reason="voice session closed")
        if task is not None:
            await task
        await self._llama.stop()
