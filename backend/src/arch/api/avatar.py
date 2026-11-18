"""Authenticated signaling and status endpoints for the optional video avatar."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from arch.api.auth import AuthServiceDependency
from arch.avatar.media import AvatarMediaService, AvatarMediaUnavailable

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


class AvatarBrowserTimingRequest(BaseModel):
    """An authenticated browser decoding point with no media payload."""

    stage: Literal["browser-first-audio-unmuted", "browser-first-video-decoded"]
    elapsed_ms: int = Field(ge=0, le=600_000)


def get_avatar_media_service(request: Request) -> AvatarMediaService:
    return cast(AvatarMediaService, request.app.state.avatar_media_service)


AvatarMediaServiceDependency = Annotated[AvatarMediaService, Depends(get_avatar_media_service)]


@router.get("/status", response_model=AvatarStatusResponse)
async def avatar_status(
    request: Request,
    auth: AuthServiceDependency,
    media: AvatarMediaServiceDependency,
) -> AvatarStatusResponse:
    """Return renderer state only to the authenticated same-origin browser."""
    auth.require_session(request)
    ready = await media.runtime_ready()
    return AvatarStatusResponse(
        enabled=ready,
        state="ready" if ready else "unavailable",
        model="wav2lip",
        source_video="configured",
        detail=None if ready else "Avatar media runtime is unavailable.",
    )


@router.post("/offer", response_model=AvatarOfferResponse)
async def avatar_offer(
    payload: AvatarOfferRequest,
    request: Request,
    auth: AuthServiceDependency,
    media: AvatarMediaServiceDependency,
) -> AvatarOfferResponse:
    """Proxy a browser offer to the authenticated loopback media runtime."""
    session_id, _ = auth.require_csrf(request)
    try:
        answer = await media.offer(session_id, payload.sdp)
    except AvatarMediaUnavailable as error:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Avatar media is unavailable.") from error
    return AvatarOfferResponse(sdp=answer.sdp, type="answer", pc_id=answer.pc_id)


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def close_avatar_session(
    request: Request,
    auth: AuthServiceDependency,
    media: AvatarMediaServiceDependency,
) -> Response:
    """Release the authenticated browser's loopback media peer."""
    session_id, _ = auth.require_csrf(request)
    await media.release(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/timing", status_code=status.HTTP_204_NO_CONTENT)
def record_avatar_browser_timing(
    payload: AvatarBrowserTimingRequest,
    request: Request,
    auth: AuthServiceDependency,
    media: AvatarMediaServiceDependency,
) -> Response:
    """Record bounded browser decode timing for the active authenticated peer."""
    session_id, _ = auth.require_csrf(request)
    try:
        media.record_browser_timing(session_id, payload.stage, payload.elapsed_ms)
    except AvatarMediaUnavailable as error:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409, detail="Avatar media session is unavailable."
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
