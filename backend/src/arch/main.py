"""HTTP application entry point for A.R.C.H."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI

from arch.api.auth import router as auth_router
from arch.api.knowledge import router as knowledge_router
from arch.api.memory import router as memory_router
from arch.api.tools import router as tools_router
from arch.api.voice import router as voice_router
from arch.auth.service import AuthService
from arch.memory.store import MemoryStore
from arch.rag.store import KnowledgeStore
from arch.settings import Settings, get_settings
from arch.tools.approvals import ApprovalStore
from arch.tools.gateway import ToolGateway
from arch.tools.policy import TerminalToolPolicy
from arch.tools.search import SearxngSearchClient
from arch.tools.terminal import TerminalExecutor
from arch.voice.models import VoiceModelPaths
from arch.voice.session import LocalVoiceSession
from arch.voice.webrtc import WebRTCSignalingService


def create_app(
    auth_service: AuthService | None = None,
    signaling_service: WebRTCSignalingService | None = None,
    memory_store: MemoryStore | None = None,
    knowledge_store: KnowledgeStore | None = None,
    approval_store: ApprovalStore | None = None,
    tool_gateway: ToolGateway | None = None,
) -> FastAPI:
    """Create the HTTP application, allowing isolated authentication tests."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Validate configuration and prepare only application-owned directories."""
        settings = get_settings()
        settings.ensure_runtime_directories()
        if not hasattr(app.state, "memory_store"):
            app.state.memory_store = MemoryStore(settings.database_path)
        app.state.memory_store.migrate()
        if not hasattr(app.state, "knowledge_store"):
            app.state.knowledge_store = KnowledgeStore(settings.database_path)
        app.state.knowledge_store.migrate()
        if not hasattr(app.state, "approval_store"):
            app.state.approval_store = ApprovalStore(settings.database_path)
        if not hasattr(app.state, "tool_gateway"):
            app.state.tool_gateway = ToolGateway(
                TerminalToolPolicy(settings.terminal_workspace_path),
                app.state.approval_store,
                TerminalExecutor(settings.terminal_workspace_path),
                search_client=(
                    SearxngSearchClient(settings.searxng_endpoint)
                    if settings.search_enabled
                    else None
                ),
            )
        if not hasattr(app.state, "auth_service"):
            app.state.auth_service = AuthService(settings.credential_path, settings.public_origin)
        if not hasattr(app.state, "signaling_service"):
            voice_session = LocalVoiceSession(
                VoiceModelPaths.from_data_dir(settings.artifact_data_dir),
                app.state.memory_store,
                database_path=settings.database_path,
                rag_model_path=settings.rag_model_path,
                tool_gateway=app.state.tool_gateway,
            )
            app.state.voice_session = voice_session
            app.state.signaling_service = WebRTCSignalingService(
                on_connection=voice_session.start,
                on_disconnection=voice_session.stop,
            )
        yield
        await app.state.signaling_service.close()

    application = FastAPI(title="A.R.C.H.", version="0.1.0", lifespan=lifespan)
    if auth_service is not None:
        application.state.auth_service = auth_service
    if signaling_service is not None:
        application.state.signaling_service = signaling_service
    if memory_store is not None:
        application.state.memory_store = memory_store
    if knowledge_store is not None:
        application.state.knowledge_store = knowledge_store
    if approval_store is not None:
        application.state.approval_store = approval_store
    if tool_gateway is not None:
        application.state.tool_gateway = tool_gateway
    application.include_router(auth_router)
    application.include_router(knowledge_router)
    application.include_router(memory_router)
    application.include_router(tools_router)
    application.include_router(voice_router)
    return application


app = create_app()


@app.get("/api/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, str]:
    """Report that the HTTP service is alive without exposing local paths."""
    return {"status": "ok", "environment": settings.environment}


@app.get("/api/ready")
def ready() -> dict[str, str]:
    """Report that application startup, migrations, and local stores completed."""
    return {"status": "ready"}
