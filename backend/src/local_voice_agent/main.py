"""HTTP application entry point for the Local Voice Agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI

from local_voice_agent.api.auth import router as auth_router
from local_voice_agent.api.knowledge import router as knowledge_router
from local_voice_agent.api.memory import router as memory_router
from local_voice_agent.api.tools import router as tools_router
from local_voice_agent.api.voice import router as voice_router
from local_voice_agent.auth.service import AuthService
from local_voice_agent.memory.store import MemoryStore
from local_voice_agent.rag.store import KnowledgeStore
from local_voice_agent.settings import Settings, get_settings
from local_voice_agent.tools.approvals import ApprovalStore
from local_voice_agent.tools.gateway import ToolGateway
from local_voice_agent.tools.policy import TerminalToolPolicy
from local_voice_agent.tools.terminal import TerminalExecutor
from local_voice_agent.voice.models import VoiceModelPaths
from local_voice_agent.voice.session import LocalVoiceSession
from local_voice_agent.voice.webrtc import WebRTCSignalingService


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
            )
        if not hasattr(app.state, "auth_service"):
            app.state.auth_service = AuthService(settings.credential_path, settings.public_origin)
        if not hasattr(app.state, "signaling_service"):
            voice_session = LocalVoiceSession(
                VoiceModelPaths.from_data_dir(settings.data_dir),
                app.state.memory_store,
                database_path=settings.database_path,
                rag_model_path=settings.rag_model_path,
                tool_gateway=app.state.tool_gateway,
            )
            app.state.signaling_service = WebRTCSignalingService(
                on_connection=voice_session.start,
                on_disconnection=voice_session.stop,
            )
        yield
        await app.state.signaling_service.close()

    application = FastAPI(title="Local Voice Agent", version="0.1.0", lifespan=lifespan)
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
