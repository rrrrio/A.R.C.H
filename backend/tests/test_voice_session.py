"""Tests for the browser-visible lifecycle of one local voice session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

from local_voice_agent.voice import session as session_module
from local_voice_agent.voice.models import VoiceModelPaths
from local_voice_agent.voice.session import LocalVoiceSession

type ReadyHandler = Callable[["FakeWorkerRunner"], Awaitable[None]]


class FakeConnection:
    """Record application messages without opening an aiortc peer connection."""

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def send_app_message(self, message: dict[str, str]) -> None:
        self.messages.append(message)


class FakeLlamaServer:
    """Provide only the session's local-model lifecycle boundary."""

    endpoint = "http://127.0.0.1:8081/v1"

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class FakeWorkerRunner:
    """Invoke the registered ready hook before returning from its runner task."""

    def __init__(self, **_: Any) -> None:
        self._ready_handler: ReadyHandler | None = None
        self.cancelled = False

    def event_handler(
        self, name: str
    ) -> Callable[[ReadyHandler], ReadyHandler]:
        assert name == "on_ready"

        def register(
            handler: ReadyHandler,
        ) -> ReadyHandler:
            self._ready_handler = handler
            return handler

        return register

    async def add_workers(self, _: object) -> None:
        return None

    async def run(self, *, auto_end: bool) -> None:
        assert not auto_end
        assert self._ready_handler is not None
        await self._ready_handler(self)
        while not self.cancelled:
            await asyncio.sleep(0)

    async def cancel(self, *, reason: str) -> None:
        assert reason == "voice session closed"
        self.cancelled = True


def test_session_announces_voice_ready_after_its_worker_is_ready(monkeypatch: Any) -> None:
    """The browser receives its speaking permission only after the worker is usable."""
    monkeypatch.setattr(session_module, "WorkerRunner", FakeWorkerRunner)
    monkeypatch.setattr(session_module, "create_pipeline_worker", lambda *_: object())
    session = LocalVoiceSession(paths=cast(VoiceModelPaths, object()))
    session._llama = FakeLlamaServer()  # type: ignore[assignment]
    connection = FakeConnection()

    async def exercise() -> None:
        await session.start(connection)  # type: ignore[arg-type]
        assert connection.messages == [{"type": "voice_ready"}]
        await session.stop()

    asyncio.run(exercise())
