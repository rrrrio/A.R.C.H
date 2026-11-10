"""Authenticated, single-session SmallWebRTC signaling ownership."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fastapi import HTTPException, status
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    ConnectionMode,
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

logger = logging.getLogger(__name__)


class SmallWebRTCHandler(Protocol):
    """The limited request-handler surface owned by the application signaling service."""

    async def handle_web_request(
        self,
        request: SmallWebRTCRequest,
        webrtc_connection_callback: Callable[[Any], Awaitable[None]],
    ) -> dict[str, str] | None: ...

    async def handle_patch_request(self, request: SmallWebRTCPatchRequest) -> None: ...

    async def close(self) -> None: ...


class WebRTCSignalingService:
    """Own one local peer connection and bind it to the authenticated browser session."""

    def __init__(
        self,
        handler: SmallWebRTCHandler | None = None,
        on_connection: Callable[[SmallWebRTCConnection], Awaitable[None]] | None = None,
        on_disconnection: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._handler = handler or SmallWebRTCRequestHandler(connection_mode=ConnectionMode.SINGLE)
        self._on_connection_callback = on_connection
        self._on_disconnection_callback = on_disconnection
        self._disconnection_notified = False
        self._owner_session_id: str | None = None
        self._owner_pc_id: str | None = None
        self._startup_task: asyncio.Task[None] | None = None

    async def offer(
        self,
        session_id: str,
        request: SmallWebRTCRequest,
    ) -> dict[str, str]:
        """Return an SDP answer before the local models finish starting."""
        self._assert_session_owns_connection(session_id, request.pc_id)
        connection: SmallWebRTCConnection | None = None

        async def capture_connection(candidate: SmallWebRTCConnection) -> None:
            nonlocal connection
            connection = candidate

        answer = await self._handler.handle_web_request(request, capture_connection)
        if answer is None:
            raise RuntimeError("SmallWebRTC did not return an SDP answer.")
        pc_id = answer.get("pc_id")
        if not isinstance(pc_id, str) or not pc_id:
            raise RuntimeError("SmallWebRTC returned an invalid peer connection identifier.")
        self._owner_session_id = session_id
        self._owner_pc_id = pc_id
        if connection is not None:
            await self._activate_connection(connection)
        return answer

    async def add_candidates(
        self,
        session_id: str,
        pc_id: str,
        candidates: list[IceCandidate],
    ) -> None:
        """Pass ICE candidates only for the currently authenticated owner connection."""
        self._assert_session_owns_connection(session_id, pc_id)
        await self._handler.handle_patch_request(
            SmallWebRTCPatchRequest(pc_id=pc_id, candidates=candidates)
        )

    async def release(self, session_id: str) -> None:
        """Close and release the single connection only for its owning session."""
        self._assert_session_owns_connection(session_id, self._owner_pc_id)
        await self._cancel_startup()
        await self._handler.close()
        self._clear_owner()
        await self._notify_disconnection()

    async def close(self) -> None:
        """Release all local peer resources during application shutdown."""
        await self._cancel_startup()
        await self._handler.close()
        self._clear_owner()
        await self._notify_disconnection()

    def _assert_session_owns_connection(self, session_id: str, pc_id: str | None) -> None:
        if self._owner_session_id is None:
            if pc_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Unknown voice session.",
                )
            return
        if session_id != self._owner_session_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Voice session is in use.",
            )
        if pc_id != self._owner_pc_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Unknown voice session.",
            )

    async def _activate_connection(self, connection: SmallWebRTCConnection) -> None:
        """Begin WebRTC immediately and load local media models in the background."""

        @connection.event_handler("closed")  # type: ignore[untyped-decorator]
        async def release_closed_connection(closed: SmallWebRTCConnection) -> None:
            if closed.pc_id == self._owner_pc_id:
                await self._cancel_startup()
                self._clear_owner()
                await self._notify_disconnection()

        self._disconnection_notified = False
        await connection.connect()  # type: ignore[no-untyped-call]
        if self._on_connection_callback is not None:
            self._startup_task = asyncio.create_task(self._start_media(connection))

    async def _start_media(self, connection: SmallWebRTCConnection) -> None:
        """Own the background startup task so a disconnect can cancel it safely."""
        try:
            assert self._on_connection_callback is not None
            await self._on_connection_callback(connection)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Local media startup failed after WebRTC connected.")
            if connection.pc_id == self._owner_pc_id:
                await self._handler.close()
                self._clear_owner()
                await self._notify_disconnection()
        finally:
            if self._startup_task is asyncio.current_task():
                self._startup_task = None

    async def _cancel_startup(self) -> None:
        """Wait for a cancelled model startup before releasing its peer connection."""
        task = self._startup_task
        self._startup_task = None
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _clear_owner(self) -> None:
        self._owner_session_id = None
        self._owner_pc_id = None

    async def _notify_disconnection(self) -> None:
        if self._disconnection_notified or self._on_disconnection_callback is None:
            return
        self._disconnection_notified = True
        await self._on_disconnection_callback()
