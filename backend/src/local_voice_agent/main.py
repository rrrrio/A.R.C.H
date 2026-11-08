"""HTTP application entry point for the Local Voice Agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI

from local_voice_agent.api.auth import router as auth_router
from local_voice_agent.auth.service import AuthService
from local_voice_agent.settings import Settings, get_settings


def create_app(auth_service: AuthService | None = None) -> FastAPI:
    """Create the HTTP application, allowing isolated authentication tests."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Validate configuration and prepare only application-owned directories."""
        settings = get_settings()
        settings.ensure_runtime_directories()
        if not hasattr(app.state, "auth_service"):
            app.state.auth_service = AuthService(settings.credential_path, settings.public_origin)
        yield

    application = FastAPI(title="Local Voice Agent", version="0.1.0", lifespan=lifespan)
    if auth_service is not None:
        application.state.auth_service = auth_service
    application.include_router(auth_router)
    return application


app = create_app()


@app.get("/api/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, str]:
    """Report that the HTTP service is alive without exposing local paths."""
    return {"status": "ok", "environment": settings.environment}
