"""Bounded in-memory sessions and request-origin enforcement."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request, WebSocket, status

from arch.auth.credentials import load_credentials, verify_password

SESSION_COOKIE_NAME = "arch_session"
TerminalProfile = Literal["observe", "assist", "operator"]


@dataclass(frozen=True)
class TerminalLease:
    """A short-lived capability marker attached to one authenticated session."""

    lease_id: str
    expires_at: float
    expires_at_iso: str
    scopes: tuple[str, ...]

    def is_active(self) -> bool:
        """Return whether the lease remains valid according to the process clock."""
        return self.expires_at > time.monotonic()


@dataclass(frozen=True)
class Session:
    """An opaque browser session with its matching CSRF secret."""

    csrf_token: str
    expires_at: float
    terminal_lease: TerminalLease | None = None
    terminal_profile: TerminalProfile = "assist"


class SessionStore:
    """Keep server-side session state in process memory so restart revokes all sessions."""

    def __init__(self, ttl_seconds: int = 8 * 60 * 60) -> None:
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, Session] = {}

    def create(self) -> tuple[str, Session]:
        """Create and return a fresh opaque session identifier and CSRF token."""
        self._discard_expired()
        session_id = secrets.token_urlsafe(32)
        lease_expires = datetime.now(UTC) + timedelta(minutes=30)
        session = Session(
            csrf_token=secrets.token_urlsafe(32),
            expires_at=time.monotonic() + self._ttl_seconds,
            terminal_lease=TerminalLease(
                lease_id=secrets.token_urlsafe(24),
                expires_at=time.monotonic() + 30 * 60,
                expires_at_iso=lease_expires.isoformat(),
                scopes=("registered_observe", "home_metadata"),
            ),
        )
        self._sessions[session_id] = session
        return session_id, session

    def set_terminal_profile(self, session_id: str, profile: TerminalProfile) -> Session | None:
        """Change only the active browser session's requested terminal ceiling."""
        if profile not in {"observe", "assist", "operator"}:
            raise ValueError("Invalid terminal profile.")
        self._discard_expired()
        session = self._sessions.get(session_id)
        if session is None:
            return None
        replacement = Session(
            csrf_token=session.csrf_token,
            expires_at=session.expires_at,
            terminal_lease=session.terminal_lease,
            terminal_profile=profile,
        )
        self._sessions[session_id] = replacement
        return replacement

    def get(self, session_id: str | None) -> Session | None:
        """Resolve a live session ID without extending its lifetime."""
        self._discard_expired()
        return self._sessions.get(session_id) if session_id else None

    def revoke(self, session_id: str | None) -> None:
        """Remove a session whether it is active, expired, or absent."""
        if session_id:
            self._sessions.pop(session_id, None)

    def reissue_terminal_lease(self, session_id: str) -> Session | None:
        """Rotate one session's short-lived read-only terminal lease."""
        self._discard_expired()
        session = self._sessions.get(session_id)
        if session is None:
            return None
        lease_expires = datetime.now(UTC) + timedelta(minutes=30)
        replacement = Session(
            csrf_token=session.csrf_token,
            expires_at=session.expires_at,
            terminal_lease=TerminalLease(
                lease_id=secrets.token_urlsafe(24),
                expires_at=time.monotonic() + 30 * 60,
                expires_at_iso=lease_expires.isoformat(),
                scopes=("registered_observe", "home_metadata"),
            ),
            terminal_profile=session.terminal_profile,
        )
        self._sessions[session_id] = replacement
        return replacement

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

    def terminal_lease(self, session_id: str | None) -> TerminalLease | None:
        """Return an active lease for a session, or None after session/lease expiry."""
        session = self.sessions.get(session_id)
        if session is None or session.terminal_lease is None:
            return None
        return session.terminal_lease if session.terminal_lease.is_active() else None

    def terminal_profile(self, session_id: str | None) -> TerminalProfile:
        """Return the active session profile, defaulting to the safest usable profile."""
        session = self.sessions.get(session_id)
        return "assist" if session is None else session.terminal_profile

    def set_terminal_profile(self, request: Request, profile: TerminalProfile) -> Session:
        """Apply a same-origin profile selection to the authenticated browser session."""
        session_id, _ = self.require_csrf(request)
        session = self.sessions.set_terminal_profile(session_id, profile)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required.",
            )
        return session

    def reissue_terminal_lease(self, request: Request, password: str) -> Session:
        """Require the current session and password before rotating its read-only lease."""
        session_id, _ = self.require_csrf(request)
        record = load_credentials(self.credential_path)
        if record is None or not verify_password(record, password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials."
            )
        session = self.sessions.reissue_terminal_lease(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
            )
        return session

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
        """Authenticate a browser WebSocket using cookie, origin, and its subprotocol token."""
        session_id = websocket.cookies.get(SESSION_COOKIE_NAME)
        session = self.sessions.get(session_id)
        origin = websocket.headers.get("origin")
        protocols = websocket.headers.get("sec-websocket-protocol", "").split(",")
        if session_id is None or session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
            )
        if origin is None or origin.rstrip("/") != self.public_origin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Invalid request origin."
            )
        expected_protocol = f"arch-csrf.{session.csrf_token}"
        valid_protocol = any(
            secrets.compare_digest(protocol.strip(), expected_protocol) for protocol in protocols
        )
        if not valid_protocol:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token.")
        return session_id, session
