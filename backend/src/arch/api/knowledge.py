"""Authenticated read-only status for the local knowledge index."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from arch.api.auth import AuthServiceDependency
from arch.rag.store import KnowledgeStore

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class KnowledgeStatusResponse(BaseModel):
    """Browser-safe summary of the current local index, without source contents."""

    source_count: int = Field(ge=0)
    indexed_at: str | None


def get_knowledge_store(request: Request) -> KnowledgeStore:
    """Read the application-scoped local knowledge repository."""
    return cast(KnowledgeStore, request.app.state.knowledge_store)


KnowledgeStoreDependency = Annotated[KnowledgeStore, Depends(get_knowledge_store)]


@router.get("/status", response_model=KnowledgeStatusResponse)
def status(
    request: Request,
    auth: AuthServiceDependency,
    store: KnowledgeStoreDependency,
) -> KnowledgeStatusResponse:
    """Report local index freshness only to the authenticated administrator."""
    auth.require_session(request)
    source_count, indexed_at = store.index_status()
    return KnowledgeStatusResponse(source_count=source_count, indexed_at=indexed_at)
