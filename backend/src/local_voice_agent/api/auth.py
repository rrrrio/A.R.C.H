"""Authenticated browser endpoints for the local administrator."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, SecretStr

from local_voice_agent.auth.service import SESSION_COOKIE_NAME, AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Password payload accepted only through the same-origin HTTPS endpoint."""

    password: SecretStr


class AuthStatusResponse(BaseModel):
    """Non-sensitive authentication state for the browser startup screen."""

    configured: bool
    authenticated: bool


class LoginResponse(BaseModel):
    """CSRF token returned after a successful login for subsequent mutations."""

    csrf_token: str


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
    authenticated = auth.sessions.get(request.cookies.get(SESSION_COOKIE_NAME)) is not None
    return AuthStatusResponse(configured=auth.is_configured, authenticated=authenticated)


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
