"""Same-origin authenticated WebRTC signaling and WebSocket media endpoints."""

from __future__ import annotations

import inspect
import logging
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Request, Response, WebSocket, status
from pipecat.transports.smallwebrtc.request_handler import IceCandidate, SmallWebRTCRequest
from pydantic import BaseModel, Field

from arch.api.auth import AuthServiceDependency
from arch.auth.service import AuthService
from arch.voice.webrtc import WebRTCSignalingService

router = APIRouter(prefix="/api/voice", tags=["voice"])
logger = logging.getLogger(__name__)


class OfferRequest(BaseModel):
    """Browser SDP offer and optional renegotiation peer identifier."""

    sdp: str = Field(min_length=1)
    type: Literal["offer"]
    pc_id: str | None = None
    restart_pc: bool | None = None


class OfferResponse(BaseModel):
    """SDP answer paired with the opaque server-generated peer identifier."""

    sdp: str
    type: Literal["answer"]
    pc_id: str


class IceCandidateRequest(BaseModel):
    """One browser ICE candidate in the field names expected by SmallWebRTC."""

    candidate: str
    sdp_mid: str
    sdp_mline_index: int


class IceCandidatesRequest(BaseModel):
    """Candidate batch for an already-owned peer connection."""

    pc_id: str = Field(min_length=1)
    candidates: list[IceCandidateRequest]


def get_signaling_service(request: Request) -> WebRTCSignalingService:
    """Read the application-scoped single-session signaling owner."""
    return cast(WebRTCSignalingService, request.app.state.signaling_service)


SignalingServiceDependency = Annotated[WebRTCSignalingService, Depends(get_signaling_service)]


@router.websocket("/stream")
async def stream(websocket: WebSocket) -> None:
    """Carry fallback PCM media through the authenticated HTTPS origin."""
    auth = cast(AuthService, websocket.app.state.auth_service)
    try:
        session_id, session = auth.require_websocket_csrf(websocket)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept(subprotocol=f"arch-csrf.{session.csrf_token}")
    voice_session = websocket.app.state.voice_session
    try:
        if "session_id" in inspect.signature(voice_session.start_websocket).parameters:
            await voice_session.start_websocket(websocket, session_id)
        else:
            await voice_session.start_websocket(websocket)
    except Exception:
        logger.exception("Voice WebSocket session failed during startup or shutdown.")
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)


@router.post("/offer", response_model=OfferResponse)
async def offer(
    payload: OfferRequest,
    request: Request,
    auth: AuthServiceDependency,
    signaling: SignalingServiceDependency,
) -> OfferResponse:
    """Create or renegotiate the caller-owned single browser peer connection."""
    session_id, _ = auth.require_csrf(request)
    answer = await signaling.offer(
        session_id,
        SmallWebRTCRequest(
            sdp=payload.sdp,
            type=payload.type,
            pc_id=payload.pc_id,
            restart_pc=payload.restart_pc,
        ),
    )
    return OfferResponse.model_validate(answer)


@router.patch("/candidates", status_code=status.HTTP_204_NO_CONTENT)
async def candidates(
    payload: IceCandidatesRequest,
    request: Request,
    auth: AuthServiceDependency,
    signaling: SignalingServiceDependency,
) -> Response:
    """Add browser ICE candidates for the authenticated owner connection only."""
    session_id, _ = auth.require_csrf(request)
    await signaling.add_candidates(
        session_id,
        payload.pc_id,
        [
            IceCandidate(
                candidate=candidate.candidate,
                sdp_mid=candidate.sdp_mid,
                sdp_mline_index=candidate.sdp_mline_index,
            )
            for candidate in payload.candidates
        ],
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def close_session(
    request: Request,
    auth: AuthServiceDependency,
    signaling: SignalingServiceDependency,
) -> Response:
    """Release the caller-owned peer connection and all local media resources."""
    session_id, _ = auth.require_csrf(request)
    await signaling.release(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
