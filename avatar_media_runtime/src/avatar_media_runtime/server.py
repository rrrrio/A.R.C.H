"""Loopback-only HTTP control surface for the avatar media runtime."""

from __future__ import annotations

import base64
import secrets

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from avatar_media_runtime.runtime import AvatarMediaRuntime, MediaPreparationError
from avatar_media_runtime.tickets import MediaTicketSigner, TicketError


class SessionOffer(BaseModel):
    """Private backend-to-runtime WebRTC offer."""

    sdp: str = Field(min_length=1)


class SessionAnswer(BaseModel):
    """Sanitized SDP response safe for the backend to proxy to its browser."""

    sdp: str
    type: str = "answer"
    pc_id: str


class UtteranceRequest(BaseModel):
    """Completed 24 kHz PCM prepared by A.R.C.H. for the private runtime."""

    utterance_id: str = Field(min_length=1)
    pcm_24khz_base64: str = Field(min_length=1)


class UtteranceCancelRequest(BaseModel):
    """Private cancellation of a prepared or playing runtime utterance."""

    utterance_id: str = Field(min_length=1)


def create_runtime_app(runtime: AvatarMediaRuntime, signer: MediaTicketSigner) -> FastAPI:
    """Create the API intended exclusively for a `127.0.0.1` listener."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Report loopback media readiness without accepting media input."""
        if not runtime.is_ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Avatar media backend is unavailable.",
            )
        return {"status": "ready"}

    @app.post("/session", response_model=SessionAnswer)
    async def create_session(
        offer: SessionOffer,
        ticket: str | None = Header(default=None, alias="X-Avatar-Media-Ticket"),
        session_id: str | None = Header(default=None, alias="X-Avatar-Session-Id"),
    ) -> SessionAnswer:
        if ticket is None or session_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized runtime request."
            )
        try:
            claims = signer.verify_and_consume(ticket, session_id)
            answer = await runtime.create_session(claims, offer.sdp)
        except TicketError as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized runtime request."
            ) from error
        except RuntimeError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Avatar session unavailable."
            ) from error
        return SessionAnswer(sdp=answer.sdp, pc_id=secrets.token_urlsafe(24))

    @app.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
    async def close_session(
        session_id: str | None = Header(default=None, alias="X-Avatar-Session-Id"),
    ) -> None:
        if session_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized runtime request."
            )
        await runtime.close_session(session_id)

    @app.post("/utterance", status_code=status.HTTP_204_NO_CONTENT)
    async def submit_utterance(
        utterance: UtteranceRequest,
        session_id: str | None = Header(default=None, alias="X-Avatar-Session-Id"),
    ) -> None:
        if session_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized runtime request."
            )
        try:
            pcm = base64.b64decode(utterance.pcm_24khz_base64, validate=True)
            await runtime.submit(session_id, utterance.utterance_id, pcm)
        except (ValueError, MediaPreparationError) as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Avatar media unavailable.",
            ) from error

    @app.post("/utterance/cancel", status_code=status.HTTP_204_NO_CONTENT)
    async def cancel_utterance(
        utterance: UtteranceCancelRequest,
        session_id: str | None = Header(default=None, alias="X-Avatar-Session-Id"),
    ) -> None:
        if session_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized runtime request."
            )
        await runtime.cancel(session_id, utterance.utterance_id)

    return app
