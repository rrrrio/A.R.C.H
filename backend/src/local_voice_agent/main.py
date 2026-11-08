"""HTTP application entry point for the Local Voice Agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI

from local_voice_agent.settings import Settings, get_settings


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Validate configuration and prepare only application-owned directories."""
    get_settings().ensure_runtime_directories()
    yield


app = FastAPI(title="Local Voice Agent", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, str]:
    """Report that the HTTP service is alive without exposing local paths."""
    return {"status": "ok", "environment": settings.environment}
