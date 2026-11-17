"""Backend ownership of short-lived tickets for the local media runtime."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AvatarMediaAnswer:
    """Sanitized WebRTC answer returned through the authenticated API."""

    sdp: str
    pc_id: str


class AvatarMediaUnavailable(RuntimeError):
    """Raised when the loopback runtime cannot own avatar media."""


class AvatarMediaRuntimeClient(Protocol):
    """Private loopback client; its ticket must never reach the browser."""

    async def create_session(
        self, *, ticket: str, session_id: str, offer_sdp: str
    ) -> AvatarMediaAnswer: ...

    async def close_session(self, *, session_id: str) -> None: ...

    async def submit_utterance(
        self, *, session_id: str, utterance_id: str, pcm_24khz: bytes
    ) -> None: ...

    async def cancel_utterance(self, *, session_id: str, utterance_id: str) -> None: ...


class LoopbackAvatarMediaRuntimeClient:
    """HTTP adapter for a runtime that is never exposed through Caddy."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 5.0) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
            raise ValueError("Avatar media runtime must use an HTTP 127.0.0.1 URL.")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Avatar media runtime URL must not include a path or query.")
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def create_session(
        self, *, ticket: str, session_id: str, offer_sdp: str
    ) -> AvatarMediaAnswer:
        headers = {
            "X-Avatar-Media-Ticket": ticket,
            "X-Avatar-Session-Id": session_id,
        }
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout_seconds
        ) as client:
            response = await client.post("/session", headers=headers, json={"sdp": offer_sdp})
        response.raise_for_status()
        payload = response.json()
        if payload.get("type") != "answer":
            raise AvatarMediaUnavailable("Avatar media runtime returned an invalid answer.")
        try:
            return AvatarMediaAnswer(sdp=str(payload["sdp"]), pc_id=str(payload["pc_id"]))
        except KeyError as error:
            raise AvatarMediaUnavailable(
                "Avatar media runtime returned an invalid answer."
            ) from error

    async def close_session(self, *, session_id: str) -> None:
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout_seconds
        ) as client:
            response = await client.delete(
                "/session", headers={"X-Avatar-Session-Id": session_id}
            )
        response.raise_for_status()

    async def submit_utterance(
        self, *, session_id: str, utterance_id: str, pcm_24khz: bytes
    ) -> None:
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout_seconds
        ) as client:
            response = await client.post(
                "/utterance",
                headers={"X-Avatar-Session-Id": session_id},
                json={
                    "utterance_id": utterance_id,
                    "pcm_24khz_base64": base64.b64encode(pcm_24khz).decode("ascii"),
                },
            )
        response.raise_for_status()

    async def cancel_utterance(self, *, session_id: str, utterance_id: str) -> None:
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout_seconds
        ) as client:
            response = await client.post(
                "/utterance/cancel",
                headers={"X-Avatar-Session-Id": session_id},
                json={"utterance_id": utterance_id},
            )
        response.raise_for_status()


class AvatarMediaService:
    """Issue opaque, short-lived tickets without exposing runtime credentials."""

    def __init__(
        self,
        *,
        runtime: AvatarMediaRuntimeClient | None = None,
        lifetime_seconds: int = 30,
        ticket_secret: bytes | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._lifetime = lifetime_seconds
        self._secret = ticket_secret or secrets.token_bytes(32)
        if len(self._secret) < 16:
            raise ValueError("Media ticket secret must contain at least 16 bytes.")
        self._runtime = runtime
        self._now = now or (lambda: datetime.now(UTC))
        self._active_sessions: set[str] = set()

    async def offer(self, session_id: str, offer_sdp: str) -> AvatarMediaAnswer:
        """Proxy an authenticated offer while retaining the ticket server-side."""
        if self._runtime is None:
            raise AvatarMediaUnavailable("Avatar media runtime is unavailable.")
        try:
            answer = await self._runtime.create_session(
                ticket=self._issue_ticket(session_id),
                session_id=session_id,
                offer_sdp=offer_sdp,
            )
            self._active_sessions.add(session_id)
            return answer
        except AvatarMediaUnavailable:
            raise
        except Exception as error:
            raise AvatarMediaUnavailable("Avatar media runtime is unavailable.") from error

    async def release(self, session_id: str) -> None:
        """Ask the private runtime to release the authenticated session peer."""
        if self._runtime is None:
            return
        try:
            await self._runtime.close_session(session_id=session_id)
        except Exception:
            # A lost loopback runtime already released its process-local peer.
            pass
        self._active_sessions.discard(session_id)

    async def submit_utterance(
        self, session_id: str, utterance_id: str, pcm_24khz: bytes
    ) -> None:
        """Send a complete answer only to the already-authorized local runtime."""
        if self._runtime is None:
            raise AvatarMediaUnavailable("Avatar media runtime is unavailable.")
        try:
            await self._runtime.submit_utterance(
                session_id=session_id, utterance_id=utterance_id, pcm_24khz=pcm_24khz
            )
        except Exception as error:
            raise AvatarMediaUnavailable("Avatar media runtime is unavailable.") from error

    async def cancel_utterance(self, session_id: str, utterance_id: str) -> None:
        """Fence both prepared and playing local media for an interrupted answer."""
        if self._runtime is None:
            return
        try:
            await self._runtime.cancel_utterance(session_id=session_id, utterance_id=utterance_id)
        except Exception:
            return

    def is_active(self, session_id: str) -> bool:
        """Report whether this authenticated browser still owns a media peer."""
        return session_id in self._active_sessions

    def record_browser_timing(self, session_id: str, stage: str, elapsed_ms: int) -> None:
        """Log an authenticated browser timing point without accepting media payloads."""
        if not self.is_active(session_id):
            raise AvatarMediaUnavailable("Avatar media session is unavailable.")
        logger.info(
            "%s",
            json.dumps(
                {
                    "elapsed_ms": elapsed_ms,
                    "event": "avatar-media-timing",
                    "monotonic_ns": time.monotonic_ns(),
                    "session_id": session_id,
                    "stage": stage,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    def _issue_ticket(self, session_id: str) -> str:
        """Create a one-use claim exclusively for the runtime request header."""
        if not session_id:
            raise ValueError("Authenticated session ID is required.")
        expires_at = self._now() + timedelta(seconds=self._lifetime)
        payload = json.dumps(
            {
                "expires_at": expires_at.astimezone(UTC).timestamp(),
                "nonce": secrets.token_urlsafe(24),
                "session_id": session_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self._secret, encoded, hashlib.sha256).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=")
        return f"{encoded.decode('ascii')}.{encoded_signature.decode('ascii')}"
