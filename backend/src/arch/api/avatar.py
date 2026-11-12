"""Authenticated signaling and status endpoints for the optional video avatar."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from arch.api.auth import AuthServiceDependency
from arch.avatar.video import AvatarVideoSignalingService

router = APIRouter(prefix="/api/avatar", tags=["avatar"])


class AvatarOfferRequest(BaseModel):
    """Browser SDP offer for a recv-only video peer."""

    sdp: str = Field(min_length=1)
    type: Literal["offer"]


class AvatarOfferResponse(BaseModel):
    """SDP answer and opaque peer identifier for one avatar session."""

    sdp: str
    type: Literal["answer"]
    pc_id: str


class AvatarStatusResponse(BaseModel):
    """Non-sensitive renderer status for the authenticated browser."""

    enabled: bool
    state: str
    model: str
    source_video: str
    detail: str | None = None


def get_avatar_service(request: Request) -> AvatarVideoSignalingService:
    """Read the application-scoped avatar signaling owner."""
    return cast(AvatarVideoSignalingService, request.app.state.avatar_video_service)


AvatarServiceDependency = Annotated[
    AvatarVideoSignalingService, Depends(get_avatar_service)
]


@router.get("/status", response_model=AvatarStatusResponse)
def avatar_status(
    request: Request,
    auth: AuthServiceDependency,
    avatar: AvatarServiceDependency,
) -> AvatarStatusResponse:
    """Return renderer state only to the authenticated same-origin browser."""
    auth.require_session(request)
    return AvatarStatusResponse(**asdict(avatar.status()))


@router.post("/offer", response_model=AvatarOfferResponse)
async def avatar_offer(
    payload: AvatarOfferRequest,
    request: Request,
    auth: AuthServiceDependency,
    avatar: AvatarServiceDependency,
) -> AvatarOfferResponse:
    """Create the owner-bound, video-only WebRTC peer."""
    session_id, _ = auth.require_csrf(request)
    answer = await avatar.offer(session_id, payload.sdp, payload.type)
    return AvatarOfferResponse.model_validate(answer)


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def close_avatar_session(
    request: Request,
    auth: AuthServiceDependency,
    avatar: AvatarServiceDependency,
) -> Response:
    """Release the authenticated browser's avatar peer and worker stream."""
    session_id, _ = auth.require_csrf(request)
    await avatar.release(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
