"""Authenticated read-only status for the local knowledge index."""

from __future__ import annotations

import asyncio
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import status as http_status
from pydantic import BaseModel, Field

from arch.api.auth import AuthServiceDependency
from arch.rag.index_service import (
    KnowledgeIndexBusyError,
    KnowledgeIndexService,
    KnowledgeIndexUnavailableError,
)
from arch.rag.store import KnowledgeStore

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class KnowledgeStatusResponse(BaseModel):
    """Browser-safe summary of the current local index, without source contents."""

    source_count: int = Field(ge=0)
    indexed_at: str | None


class KnowledgeReindexResponse(KnowledgeStatusResponse):
    """Browser-safe result of one completed fixed-inbox rebuild."""

    changed_source_count: int = Field(ge=0)


class KnowledgeSourceResponse(BaseModel):
    """One locally indexed source name and its index timestamp, without contents."""

    path: str
    updated_at: str


def get_knowledge_store(request: Request) -> KnowledgeStore:
    """Read the application-scoped local knowledge repository."""
    return cast(KnowledgeStore, request.app.state.knowledge_store)


KnowledgeStoreDependency = Annotated[KnowledgeStore, Depends(get_knowledge_store)]


def get_knowledge_index_service(request: Request) -> KnowledgeIndexService:
    """Read the application-owned rebuild service without exposing its inputs."""
    return cast(KnowledgeIndexService, request.app.state.knowledge_index_service)


KnowledgeIndexServiceDependency = Annotated[
    KnowledgeIndexService, Depends(get_knowledge_index_service)
]


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


@router.get("/sources", response_model=list[KnowledgeSourceResponse])
def sources(
    request: Request,
    auth: AuthServiceDependency,
    store: KnowledgeStoreDependency,
    query: str = "",
) -> list[KnowledgeSourceResponse]:
    """List locally indexed source metadata for the authenticated archive browser."""
    auth.require_session(request)
    return [
        KnowledgeSourceResponse(path=source.path, updated_at=source.updated_at)
        for source in store.list_sources(query)
    ]


@router.post("/reindex", response_model=KnowledgeReindexResponse)
async def reindex(
    request: Request,
    auth: AuthServiceDependency,
    store: KnowledgeStoreDependency,
    index_service: KnowledgeIndexServiceDependency,
) -> KnowledgeReindexResponse:
    """Rebuild only the configured inbox after same-origin administrator authorization."""
    auth.require_csrf(request)
    try:
        changed_source_count = await asyncio.to_thread(index_service.rebuild)
    except KnowledgeIndexBusyError as error:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT, "Knowledge indexing is already running."
        ) from error
    except KnowledgeIndexUnavailableError as error:
        raise HTTPException(
            http_status.HTTP_503_SERVICE_UNAVAILABLE, "Local knowledge model is not ready."
        ) from error
    except Exception as error:
        raise HTTPException(
            http_status.HTTP_503_SERVICE_UNAVAILABLE, "Local knowledge indexing is unavailable."
        ) from error
    source_count, indexed_at = store.index_status()
    return KnowledgeReindexResponse(
        changed_source_count=changed_source_count,
        source_count=source_count,
        indexed_at=indexed_at,
    )
