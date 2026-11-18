"""HTTP application entry point for A.R.C.H."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI

from arch.api.auth import router as auth_router
from arch.api.avatar import router as avatar_router
from arch.api.conversation_profiles import router as conversation_profiles_router
from arch.api.knowledge import router as knowledge_router
from arch.api.memory import router as memory_router
from arch.api.tools import router as tools_router
from arch.api.voice import router as voice_router
from arch.api.voices import router as voices_router
from arch.auth.service import AuthService
from arch.avatar.media import AvatarMediaService, LoopbackAvatarMediaRuntimeClient
from arch.avatar.media_adapter import AvatarMediaAdapter
from arch.conversation.profiles import ConversationProfileStore
from arch.memory.store import MemoryStore
from arch.rag.index_service import KnowledgeIndexService, LocalKnowledgeIndexService
from arch.rag.store import KnowledgeStore
from arch.settings import Settings, get_settings
from arch.tools.approvals import ApprovalStore
from arch.tools.gateway import ToolGateway
from arch.tools.policy import TerminalToolPolicy
from arch.tools.search import SearxngSearchClient
from arch.tools.terminal import TerminalExecutor
from arch.voice.models import VoiceModelPaths
from arch.voice.profiles import VoiceProfileStore
from arch.voice.reference_transcriber import ReferenceTranscriber, VoiceReferenceTranscriber
from arch.voice.session import LocalVoiceSession
from arch.voice.webrtc import WebRTCSignalingService


def create_app(
    auth_service: AuthService | None = None,
    signaling_service: WebRTCSignalingService | None = None,
    memory_store: MemoryStore | None = None,
    knowledge_store: KnowledgeStore | None = None,
    knowledge_index_service: KnowledgeIndexService | None = None,
    approval_store: ApprovalStore | None = None,
    tool_gateway: ToolGateway | None = None,
    voice_profile_store: VoiceProfileStore | None = None,
    voice_reference_transcriber: ReferenceTranscriber | None = None,
    avatar_media_service: AvatarMediaService | None = None,
    conversation_profile_store: ConversationProfileStore | None = None,
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
        if not hasattr(app.state, "conversation_profile_store"):
            app.state.conversation_profile_store = ConversationProfileStore(settings.database_path)
        app.state.conversation_profile_store.migrate()
        if not hasattr(app.state, "knowledge_store"):
            app.state.knowledge_store = KnowledgeStore(settings.database_path)
        app.state.knowledge_store.migrate()
        if not hasattr(app.state, "knowledge_index_service"):
            app.state.knowledge_index_service = LocalKnowledgeIndexService(
                settings.knowledge_inbox_path,
                settings.rag_model_path,
                app.state.knowledge_store,
            )
        if not hasattr(app.state, "auth_service"):
            app.state.auth_service = AuthService(settings.credential_path, settings.public_origin)
        if not hasattr(app.state, "approval_store"):
            app.state.approval_store = ApprovalStore(settings.database_path)
        if not hasattr(app.state, "tool_gateway"):
            workspace = settings.terminal_workspace_path
            app.state.tool_gateway = ToolGateway(
                TerminalToolPolicy(workspace, home_root=Path.home()),
                app.state.approval_store,
                TerminalExecutor(workspace),
                search_client=(
                    SearxngSearchClient(settings.searxng_endpoint)
                    if settings.search_enabled
                    else None
                ),
                profile_provider=app.state.auth_service.terminal_profile,
            )
        if not hasattr(app.state, "voice_profile_store"):
            app.state.voice_profile_store = VoiceProfileStore(settings.data_dir)
        if not hasattr(app.state, "voice_reference_transcriber"):
            app.state.voice_reference_transcriber = voice_reference_transcriber or (
                VoiceReferenceTranscriber(VoiceModelPaths.from_data_dir(settings.artifact_data_dir))
            )
        if not hasattr(app.state, "signaling_service"):
            voice_session = LocalVoiceSession(
                VoiceModelPaths.from_data_dir(settings.artifact_data_dir),
                app.state.memory_store,
                database_path=settings.database_path,
                rag_model_path=settings.rag_model_path,
                tool_gateway=app.state.tool_gateway,
                voice_profile_store=app.state.voice_profile_store,
                conversation_profile_store=app.state.conversation_profile_store,
                data_dir=settings.data_dir,
            )
            app.state.voice_session = voice_session
            app.state.signaling_service = WebRTCSignalingService(
                on_connection=voice_session.start,
                on_disconnection=voice_session.stop,
            )
        if not hasattr(app.state, "avatar_media_service"):
            ticket_secret = settings.avatar_media_ticket_secret
            app.state.avatar_media_service = avatar_media_service or AvatarMediaService(
                runtime=(
                    LoopbackAvatarMediaRuntimeClient(settings.avatar_media_runtime_url)
                    if settings.video_avatar_enabled and ticket_secret is not None
                    else None
                ),
                ticket_secret=(
                    ticket_secret.get_secret_value().encode("utf-8")
                    if ticket_secret is not None
                    else None
                ),
            )
        managed_voice_session = getattr(app.state, "voice_session", None)
        if isinstance(managed_voice_session, LocalVoiceSession):
            managed_voice_session.set_avatar_observer_factory(
                lambda session_id: AvatarMediaAdapter(app.state.avatar_media_service, session_id)
            )
        yield
        await app.state.signaling_service.close()
        managed_voice_session = getattr(app.state, "voice_session", None)
        if isinstance(managed_voice_session, LocalVoiceSession):
            await managed_voice_session.close()
        app.state.voice_reference_transcriber.close()

    application = FastAPI(title="A.R.C.H.", version="0.1.0", lifespan=lifespan)
    if auth_service is not None:
        application.state.auth_service = auth_service
    if signaling_service is not None:
        application.state.signaling_service = signaling_service
    if memory_store is not None:
        application.state.memory_store = memory_store
    if knowledge_store is not None:
        application.state.knowledge_store = knowledge_store
    if knowledge_index_service is not None:
        application.state.knowledge_index_service = knowledge_index_service
    if approval_store is not None:
        application.state.approval_store = approval_store
    if tool_gateway is not None:
        application.state.tool_gateway = tool_gateway
    if voice_profile_store is not None:
        application.state.voice_profile_store = voice_profile_store
    if voice_reference_transcriber is not None:
        application.state.voice_reference_transcriber = voice_reference_transcriber
    if avatar_media_service is not None:
        application.state.avatar_media_service = avatar_media_service
    if conversation_profile_store is not None:
        application.state.conversation_profile_store = conversation_profile_store
    application.include_router(auth_router)
    application.include_router(avatar_router)
    application.include_router(conversation_profiles_router)
    application.include_router(knowledge_router)
    application.include_router(memory_router)
    application.include_router(tools_router)
    application.include_router(voice_router)
    application.include_router(voices_router)
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
