"""Bounded in-memory sessions and request-origin enforcement."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request, WebSocket, status

from local_voice_agent.auth.credentials import load_credentials, verify_password

SESSION_COOKIE_NAME = "lva_session"


@dataclass(frozen=True)
class Session:
    """An opaque browser session with its matching CSRF secret."""

    csrf_token: str
    expires_at: float


class SessionStore:
    """Keep server-side session state in process memory so restart revokes all sessions."""

    def __init__(self, ttl_seconds: int = 8 * 60 * 60) -> None:
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, Session] = {}

    def create(self) -> tuple[str, Session]:
        """Create and return a fresh opaque session identifier and CSRF token."""
        self._discard_expired()
        session_id = secrets.token_urlsafe(32)
        session = Session(
            csrf_token=secrets.token_urlsafe(32),
            expires_at=time.monotonic() + self._ttl_seconds,
        )
        self._sessions[session_id] = session
        return session_id, session

    def get(self, session_id: str | None) -> Session | None:
        """Resolve a live session ID without extending its lifetime."""
        self._discard_expired()
        return self._sessions.get(session_id) if session_id else None

    def revoke(self, session_id: str | None) -> None:
        """Remove a session whether it is active, expired, or absent."""
        if session_id:
            self._sessions.pop(session_id, None)

    def _discard_expired(self) -> None:
        now = time.monotonic()
        for session_id, session in tuple(self._sessions.items()):
            if session.expires_at <= now:
                self._sessions.pop(session_id, None)


class AuthService:
    """Authenticate the one configured local administrator and protect mutations."""

    def __init__(self, credential_path: Path, public_origin: str) -> None:
        self.credential_path = credential_path
        self.public_origin = public_origin.rstrip("/")
        self.sessions = SessionStore()

    @property
    def is_configured(self) -> bool:
        """Report whether the local provisioning CLI has written credentials."""
        return self.credential_path.is_file()

    def login(self, request: Request, password: str) -> tuple[str, Session]:
        """Verify request origin and password before minting a new browser session."""
        self.require_expected_origin(request)
        record = load_credentials(self.credential_path)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Administrator password has not been provisioned.",
            )
        if not verify_password(record, password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials.",
            )
        return self.sessions.create()

    def require_session(self, request: Request) -> tuple[str, Session]:
        """Return the cookie-backed session or reject anonymous requests."""
        session_id = request.cookies.get(SESSION_COOKIE_NAME)
        session = self.sessions.get(session_id)
        if session is None or session_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required.",
            )
        return session_id, session

    def require_expected_origin(self, request: Request) -> None:
        """Reject state-changing requests that do not come from the configured HTTPS origin."""
        origin = request.headers.get("origin")
        if origin is None or origin.rstrip("/") != self.public_origin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid request origin.",
            )

    def require_csrf(self, request: Request) -> tuple[str, Session]:
        """Require an authenticated session, exact origin, and matching CSRF token."""
        session_id, session = self.require_session(request)
        self.require_expected_origin(request)
        provided_token = request.headers.get("x-csrf-token")
        if provided_token is None or not secrets.compare_digest(provided_token, session.csrf_token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token.")
        return session_id, session

    def require_websocket_csrf(self, websocket: WebSocket) -> tuple[str, Session]:
        """Authenticate a browser WebSocket using its cookie, origin, and CSRF query token."""
        session_id = websocket.cookies.get(SESSION_COOKIE_NAME)
        session = self.sessions.get(session_id)
        origin = websocket.headers.get("origin")
        token = websocket.query_params.get("csrf")
        if session_id is None or session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
            )
        if origin is None or origin.rstrip("/") != self.public_origin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Invalid request origin."
            )
        if token is None or not secrets.compare_digest(token, session.csrf_token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token.")
        return session_id, session
