"""Authenticated candidate-memory review endpoints."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from local_voice_agent.api.auth import AuthServiceDependency
from local_voice_agent.memory.context import MAX_MEMORY_CANDIDATE_CHARS
from local_voice_agent.memory.store import MemoryCandidate, MemoryStore

router = APIRouter(prefix="/api/memory", tags=["memory"])


class CandidateResponse(BaseModel):
    """Browser-safe representation of a reviewable candidate memory."""

    id: str
    source_message_id: int
    content: str
    status: Literal["pending", "approved", "rejected", "deleted"]
    created_at: str
    updated_at: str


class CandidateUpdateRequest(BaseModel):
    """One browser-requested content edit or allowed approval state transition."""

    content: str | None = Field(default=None, min_length=1, max_length=MAX_MEMORY_CANDIDATE_CHARS)
    action: Literal["approved", "rejected", "deleted"] | None = None


def get_memory_store(request: Request) -> MemoryStore:
    """Read the application-scoped persistent memory repository."""
    return cast(MemoryStore, request.app.state.memory_store)


MemoryStoreDependency = Annotated[MemoryStore, Depends(get_memory_store)]


@router.get("/candidates", response_model=list[CandidateResponse])
def candidates(
    request: Request, auth: AuthServiceDependency, store: MemoryStoreDependency
) -> list[CandidateResponse]:
    """List only pending candidates for the authenticated local administrator."""
    auth.require_session(request)
    return [_candidate_response(candidate) for candidate in store.list_candidates("pending")]


@router.patch("/candidates/{candidate_id}", response_model=CandidateResponse)
def update_candidate(
    candidate_id: str,
    payload: CandidateUpdateRequest,
    request: Request,
    auth: AuthServiceDependency,
    store: MemoryStoreDependency,
) -> CandidateResponse:
    """Apply an audited edit and/or legal state change after CSRF validation."""
    session_id, _ = auth.require_csrf(request)
    if payload.content is None and payload.action is None:
        raise HTTPException(status_code=422, detail="Specify candidate content or an action.")
    try:
        candidate = None
        if payload.content is not None:
            candidate = store.edit_candidate(candidate_id, payload.content, session_id)
        if payload.action is not None:
            candidate = store.decide_candidate(candidate_id, payload.action, session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Unknown memory candidate.") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if candidate is None:
        raise RuntimeError("Candidate update did not produce a result.")
    return _candidate_response(candidate)


def _candidate_response(candidate: MemoryCandidate) -> CandidateResponse:
    return CandidateResponse(
        id=candidate.id,
        source_message_id=candidate.source_message_id,
        content=candidate.content,
        status=candidate.status,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
    )
