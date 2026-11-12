"""Authenticated browser endpoints for the local administrator."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field, SecretStr

from arch.auth.service import SESSION_COOKIE_NAME, AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Password payload accepted only through the same-origin HTTPS endpoint."""

    password: SecretStr


class AuthStatusResponse(BaseModel):
    """Non-sensitive authentication state for the browser startup screen."""

    configured: bool
    authenticated: bool
    terminal_lease_expires_at: str | None = None
    terminal_lease_scopes: list[str] = Field(default_factory=list)
    terminal_profile: Literal["observe", "assist", "operator"] | None = None


class LoginResponse(BaseModel):
    """CSRF token returned after a successful login for subsequent mutations."""

    csrf_token: str


class TerminalLeaseReissueRequest(BaseModel):
    """Password payload used to rotate an expired read-only terminal lease."""

    password: SecretStr


class TerminalLeaseResponse(BaseModel):
    """Current non-sensitive read-only terminal lease metadata."""

    terminal_lease_expires_at: str
    terminal_lease_scopes: list[str]


class TerminalProfileRequest(BaseModel):
    """A browser-selected session-only ceiling for local host tools."""

    profile: Literal["observe", "assist", "operator"]


class CsrfResponse(BaseModel):
    """CSRF token for an already authenticated browser after a page refresh."""

    csrf_token: str


def get_auth_service(request: Request) -> AuthService:
    """Read the application-scoped local authentication service."""
    return cast(AuthService, request.app.state.auth_service)


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]


@router.get("/status", response_model=AuthStatusResponse)
def auth_status(request: Request, auth: AuthServiceDependency) -> AuthStatusResponse:
    """Expose provisioning state without disclosing any credential material."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    authenticated = auth.sessions.get(session_id) is not None
    lease = auth.terminal_lease(session_id)
    return AuthStatusResponse(
        configured=auth.is_configured,
        authenticated=authenticated,
        terminal_lease_expires_at=None if lease is None else lease.expires_at_iso,
        terminal_lease_scopes=[] if lease is None else list(lease.scopes),
        terminal_profile=None if not authenticated else auth.terminal_profile(session_id),
    )


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> LoginResponse:
    """Exchange a verified password for an opaque Secure and HttpOnly session cookie."""
    session_id, session = auth.login(request, payload.password.get_secret_value())
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="strict",
        secure=True,
        max_age=8 * 60 * 60,
        path="/",
    )
    return LoginResponse(csrf_token=session.csrf_token)


@router.get("/csrf", response_model=CsrfResponse)
def csrf(request: Request, auth: AuthServiceDependency) -> CsrfResponse:
    """Return the per-session token without changing server-side state."""
    _, session = auth.require_session(request)
    return CsrfResponse(csrf_token=session.csrf_token)


@router.post("/terminal-lease/reissue", response_model=TerminalLeaseResponse)
def reissue_terminal_lease(
    payload: TerminalLeaseReissueRequest,
    request: Request,
    auth: AuthServiceDependency,
) -> TerminalLeaseResponse:
    """Rotate the current session's read-only lease after password re-authentication."""
    session = auth.reissue_terminal_lease(request, payload.password.get_secret_value())
    lease = session.terminal_lease
    assert lease is not None
    return TerminalLeaseResponse(
        terminal_lease_expires_at=lease.expires_at_iso,
        terminal_lease_scopes=list(lease.scopes),
    )


@router.put("/terminal-profile", response_model=AuthStatusResponse)
def set_terminal_profile(
    payload: TerminalProfileRequest, request: Request, auth: AuthServiceDependency
) -> AuthStatusResponse:
    """Set a profile only for the currently authenticated browser session."""
    session = auth.set_terminal_profile(request, payload.profile)
    lease = session.terminal_lease
    return AuthStatusResponse(
        configured=auth.is_configured,
        authenticated=True,
        terminal_lease_expires_at=None if lease is None else lease.expires_at_iso,
        terminal_lease_scopes=[] if lease is None else list(lease.scopes),
        terminal_profile=payload.profile,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, auth: AuthServiceDependency) -> None:
    """Revoke one session after validating its origin and CSRF proof."""
    session_id, _ = auth.require_csrf(request)
    auth.sessions.revoke(session_id)
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
