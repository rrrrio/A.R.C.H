"""Tests for the browser-visible lifecycle of one local voice session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest

from arch.voice import session as session_module
from arch.voice.models import VoiceModelPaths
from arch.voice.profiles import VoiceProfile
from arch.voice.session import LocalVoiceSession

type ReadyHandler = Callable[["FakeWorkerRunner"], Awaitable[None]]


class FakeConnection:
    """Record application messages without opening an aiortc peer connection."""

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def send_app_message(self, message: dict[str, str]) -> None:
        self.messages.append(message)


class FakeWebSocket:
    """Record fallback events without opening a network socket."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_text(self, message: str) -> None:
        self.messages.append(message)


class FakeLlamaServer:
    """Provide only the session's local-model lifecycle boundary."""

    endpoint = "http://127.0.0.1:8081/v1"

    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def stop(self) -> None:
        self.stop_calls += 1


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


class FakeProfileStore:
    """Expose one selected local profile without filesystem setup in session tests."""

    profile = VoiceProfile(
        "00000000-0000-0000-0000-000000000000", "授权声音", "参考文本", 24000, 1000, "now"
    )

    def selected_id(self) -> str:
        return self.profile.id

    def get(self, profile_id: str) -> VoiceProfile | None:
        return self.profile if profile_id == self.profile.id else None


class SwitchableProfileStore(FakeProfileStore):
    """Allow tests to model selecting Kokoro after a clone session was stopped."""

    def __init__(self) -> None:
        self.clone_selected = True

    def selected_id(self) -> str | None:
        return self.profile.id if self.clone_selected else None


class FakeCloneRuntime:
    """Record the selected-profile runtime lifecycle without launching a process."""

    instances: list[FakeCloneRuntime] = []

    def __init__(self, _: object) -> None:
        self.started = False
        self.stopped = False
        self.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def synthesize(self, _: str, __: VoiceProfile) -> tuple[bytes, int]:
        return b"\x00\x00", 24000

    async def cancel(self) -> None:
        self.stopped = True


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


def test_selected_profile_starts_and_stops_qwen_runtime_without_kokoro_fallback(
    monkeypatch: Any, tmp_path: Any
) -> None:
    """A chosen clone profile reaches the pipeline only with its owned runtime callable."""
    captured: list[tuple[object, ...]] = []
    FakeCloneRuntime.instances.clear()
    monkeypatch.setattr(session_module, "WorkerRunner", FakeWorkerRunner)
    monkeypatch.setattr(session_module, "QwenCloneRuntime", FakeCloneRuntime)

    def create_worker(*args: object) -> object:
        captured.append(args)
        return object()

    monkeypatch.setattr(session_module, "create_pipeline_worker", create_worker)
    session = LocalVoiceSession(
        paths=cast(VoiceModelPaths, object()),
        voice_profile_store=FakeProfileStore(),  # type: ignore[arg-type]
        data_dir=tmp_path,
    )
    session._llama = FakeLlamaServer()  # type: ignore[assignment]
    connection = FakeConnection()

    async def exercise() -> None:
        await session.start(connection)  # type: ignore[arg-type]
        assert captured[0][-3] == FakeProfileStore.profile
        assert callable(captured[0][-2])
        assert callable(captured[0][-1])
        await session.stop()
        await session.close()

    asyncio.run(exercise())

    assert FakeCloneRuntime.instances[0].started
    assert FakeCloneRuntime.instances[0].stopped


def test_session_reuses_qwen_runtime_across_media_reconnects(
    monkeypatch: Any, tmp_path: Any
) -> None:
    """A media reconnect must not reload the selected clone model worker."""
    FakeCloneRuntime.instances.clear()
    monkeypatch.setattr(session_module, "WorkerRunner", FakeWorkerRunner)
    monkeypatch.setattr(session_module, "QwenCloneRuntime", FakeCloneRuntime)
    monkeypatch.setattr(session_module, "create_pipeline_worker", lambda *_: object())
    session = LocalVoiceSession(
        paths=cast(VoiceModelPaths, object()),
        voice_profile_store=FakeProfileStore(),  # type: ignore[arg-type]
        data_dir=tmp_path,
    )
    session._llama = FakeLlamaServer()  # type: ignore[assignment]

    async def exercise() -> None:
        await session.start(FakeConnection())  # type: ignore[arg-type]
        await session.stop()
        await session.start(FakeConnection())  # type: ignore[arg-type]
        await session.stop()
        await session.close()

    asyncio.run(exercise())

    assert len(FakeCloneRuntime.instances) == 1
    assert FakeCloneRuntime.instances[0].stopped


def test_session_releases_qwen_runtime_when_selection_changes_to_kokoro(
    monkeypatch: Any, tmp_path: Any
) -> None:
    """Clearing the selected clone before disconnect releases its idle worker."""
    FakeCloneRuntime.instances.clear()
    monkeypatch.setattr(session_module, "WorkerRunner", FakeWorkerRunner)
    monkeypatch.setattr(session_module, "QwenCloneRuntime", FakeCloneRuntime)
    monkeypatch.setattr(session_module, "create_pipeline_worker", lambda *_: object())
    store = SwitchableProfileStore()
    session = LocalVoiceSession(
        paths=cast(VoiceModelPaths, object()),
        voice_profile_store=store,  # type: ignore[arg-type]
        data_dir=tmp_path,
    )
    session._llama = FakeLlamaServer()  # type: ignore[assignment]

    async def exercise() -> None:
        await session.start(FakeConnection())  # type: ignore[arg-type]
        store.clone_selected = False
        await session.stop()

    asyncio.run(exercise())

    assert len(FakeCloneRuntime.instances) == 1
    assert FakeCloneRuntime.instances[0].stopped


def test_session_keeps_llama_server_alive_across_media_reconnects(
    monkeypatch: Any,
) -> None:
    """A media reconnect must not reload the shared local LLM server."""
    monkeypatch.setattr(session_module, "WorkerRunner", FakeWorkerRunner)
    monkeypatch.setattr(session_module, "create_pipeline_worker", lambda *_: object())
    session = LocalVoiceSession(paths=cast(VoiceModelPaths, object()))
    llama = FakeLlamaServer()
    session._llama = llama  # type: ignore[assignment]

    async def exercise() -> None:
        await session.start(FakeConnection())  # type: ignore[arg-type]
        await session.stop()
        await session.start(FakeConnection())  # type: ignore[arg-type]
        await session.stop()
        assert llama.stop_calls == 0
        await session.close()

    asyncio.run(exercise())

    assert llama.start_calls == 2
    assert llama.stop_calls == 1


def test_websocket_reconnect_replaces_stale_runner_for_same_session(
    monkeypatch: Any,
) -> None:
    """A same-session fallback reconnect waits for the previous runner to release the session."""
    monkeypatch.setattr(session_module, "WorkerRunner", FakeWorkerRunner)

    def create_worker(*_: object, **__: object) -> object:
        return object()

    monkeypatch.setattr(session_module, "create_websocket_pipeline_worker", create_worker)
    session = LocalVoiceSession(paths=cast(VoiceModelPaths, object()))
    session._llama = FakeLlamaServer()  # type: ignore[assignment]
    first = FakeWebSocket()
    second = FakeWebSocket()

    async def exercise() -> None:
        first_task = asyncio.create_task(
            session.start_websocket(cast(Any, first), "browser-session")
        )
        await asyncio.wait_for(_wait_for_message(first, '{"type":"voice_ready"}'), timeout=1)

        second_task = asyncio.create_task(
            session.start_websocket(cast(Any, second), "browser-session")
        )
        await asyncio.wait_for(_wait_for_message(second, '{"type":"voice_ready"}'), timeout=1)

        await session.stop()
        await asyncio.gather(first_task, second_task)
        await session.close()

    asyncio.run(exercise())


def test_websocket_reconnect_from_different_session_is_rejected(
    monkeypatch: Any,
) -> None:
    """A second authenticated browser cannot replace the active session's runner."""
    monkeypatch.setattr(session_module, "WorkerRunner", FakeWorkerRunner)

    def create_worker(*_: object, **__: object) -> object:
        return object()

    monkeypatch.setattr(session_module, "create_websocket_pipeline_worker", create_worker)
    session = LocalVoiceSession(paths=cast(VoiceModelPaths, object()))
    session._llama = FakeLlamaServer()  # type: ignore[assignment]
    first = FakeWebSocket()

    async def exercise() -> None:
        first_task = asyncio.create_task(session.start_websocket(cast(Any, first), "owner"))
        await asyncio.wait_for(_wait_for_message(first, '{"type":"voice_ready"}'), timeout=1)

        with pytest.raises(RuntimeError, match="Voice session is in use"):
            await session.start_websocket(cast(Any, FakeWebSocket()), "other")

        await session.stop()
        await first_task
        await session.close()

    asyncio.run(exercise())


def test_websocket_worker_uses_session_bound_avatar_observer_factory(
    monkeypatch: Any,
) -> None:
    """The WebSocket fallback must retain avatar media ownership for its authenticated browser."""
    captured: dict[str, object] = {}
    observer = object()
    monkeypatch.setattr(session_module, "WorkerRunner", FakeWorkerRunner)

    def create_worker(*_: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    def create_observer(session_id: str) -> object | None:
        return observer if session_id == "browser-session" else None

    monkeypatch.setattr(session_module, "create_websocket_pipeline_worker", create_worker)
    session = LocalVoiceSession(
        paths=cast(VoiceModelPaths, object()),
        avatar_observer_factory=create_observer,  # type: ignore[arg-type]
    )
    session._llama = FakeLlamaServer()  # type: ignore[assignment]
    websocket = FakeWebSocket()

    async def exercise() -> None:
        task = asyncio.create_task(session.start_websocket(cast(Any, websocket), "browser-session"))
        await asyncio.wait_for(_wait_for_message(websocket, '{"type":"voice_ready"}'), timeout=1)
        await session.stop()
        await task
        await session.close()

    asyncio.run(exercise())

    assert captured["session_id"] == "browser-session"
    assert captured["avatar_observer"] is observer


async def _wait_for_message(websocket: FakeWebSocket, expected: str) -> None:
    """Wait until a test socket receives the requested lifecycle message."""
    while expected not in websocket.messages:
        await asyncio.sleep(0)
