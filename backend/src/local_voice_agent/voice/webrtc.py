"""Authenticated, single-session SmallWebRTC signaling ownership."""

from __future__ import annotations

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

    def __init__(self, handler: SmallWebRTCHandler | None = None) -> None:
        self._handler = handler or SmallWebRTCRequestHandler(connection_mode=ConnectionMode.SINGLE)
        self._owner_session_id: str | None = None
        self._owner_pc_id: str | None = None

    async def offer(
        self,
        session_id: str,
        request: SmallWebRTCRequest,
    ) -> dict[str, str]:
        """Create or renegotiate the one connection after checking its session ownership."""
        self._assert_session_owns_connection(session_id, request.pc_id)
        answer = await self._handler.handle_web_request(request, self._on_connection)
        if answer is None:
            raise RuntimeError("SmallWebRTC did not return an SDP answer.")
        pc_id = answer.get("pc_id")
        if not isinstance(pc_id, str) or not pc_id:
            raise RuntimeError("SmallWebRTC returned an invalid peer connection identifier.")
        self._owner_session_id = session_id
        self._owner_pc_id = pc_id
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
        await self._handler.close()
        self._clear_owner()

    async def close(self) -> None:
        """Release all local peer resources during application shutdown."""
        await self._handler.close()
        self._clear_owner()

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

    async def _on_connection(self, connection: SmallWebRTCConnection) -> None:
        """Start connection state handling and release ownership on an ungraceful close."""

        @connection.event_handler("closed")  # type: ignore[untyped-decorator]
        async def release_closed_connection(closed: SmallWebRTCConnection) -> None:
            if closed.pc_id == self._owner_pc_id:
                self._clear_owner()

        await connection.connect()  # type: ignore[no-untyped-call]

    def _clear_owner(self) -> None:
        self._owner_session_id = None
        self._owner_pc_id = None
