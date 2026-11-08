"""Authenticated browser review endpoints for constrained local tool approvals."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from local_voice_agent.agent.core import ToolRequest
from local_voice_agent.api.auth import AuthServiceDependency
from local_voice_agent.tools.approvals import ApprovalStore, ToolApproval
from local_voice_agent.tools.gateway import ToolGateway, ToolOutcome

router = APIRouter(prefix="/api/tools", tags=["tools"])


class ToolApprovalResponse(BaseModel):
    """Browser-safe record of one precise, expiring local tool request."""

    id: str
    tool_name: str
    argv_json: str
    working_directory: str
    reason: str
    status: Literal["pending", "approved", "rejected", "expired", "consumed"]
    created_at: str
    expires_at: str
    decided_at: str | None
    decided_by: str | None
    outcome: Literal["completed", "approval_required", "denied", "unavailable"] | None = None
    output: str | None = None


class ToolApprovalDecision(BaseModel):
    """The only browser decisions allowed for a pending tool request."""

    action: Literal["approved", "rejected"]


def get_approval_store(request: Request) -> ApprovalStore:
    """Read the application-scoped tool approval repository."""
    return cast(ApprovalStore, request.app.state.approval_store)


def get_tool_gateway(request: Request) -> ToolGateway:
    """Read the application-scoped constrained tool gateway."""
    return cast(ToolGateway, request.app.state.tool_gateway)


ApprovalStoreDependency = Annotated[ApprovalStore, Depends(get_approval_store)]
ToolGatewayDependency = Annotated[ToolGateway, Depends(get_tool_gateway)]


@router.get("/approvals", response_model=list[ToolApprovalResponse])
def approvals(
    request: Request, auth: AuthServiceDependency, store: ApprovalStoreDependency
) -> list[ToolApprovalResponse]:
    """List the authenticated administrator's live pending tool operations."""
    auth.require_session(request)
    return [_approval_response(approval) for approval in store.list_pending()]


@router.patch("/approvals/{approval_id}", response_model=ToolApprovalResponse)
async def decide_approval(
    approval_id: str,
    payload: ToolApprovalDecision,
    request: Request,
    auth: AuthServiceDependency,
    store: ApprovalStoreDependency,
    gateway: ToolGatewayDependency,
) -> ToolApprovalResponse:
    """Reject or atomically consume one approved request through the constrained gateway."""
    session_id, _ = auth.require_csrf(request)
    existing = store.get(approval_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Unknown tool approval.")
    if payload.action == "approved" and existing.request_arguments_json is None:
        raise HTTPException(
            status_code=409, detail="This legacy tool approval cannot be safely replayed."
        )
    try:
        approval = store.decide(approval_id, payload.action, session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Unknown tool approval.") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if payload.action == "rejected":
        return _approval_response(approval)
    assert approval.request_arguments_json is not None
    outcome = await gateway.execute_approved(
        approval.id,
        ToolRequest(approval.tool_name, approval.request_arguments_json),
        session_id,
    )
    consumed = store.get(approval.id)
    if consumed is None:
        raise RuntimeError("Consumed tool approval was not found.")
    return _approval_response(consumed, outcome)


def _approval_response(
    approval: ToolApproval, outcome: ToolOutcome | None = None
) -> ToolApprovalResponse:
    output = None
    if outcome is not None and outcome.result is not None:
        output = outcome.result.stdout or outcome.result.stderr
    return ToolApprovalResponse(
        id=approval.id,
        tool_name=approval.tool_name,
        argv_json=approval.argv_json,
        working_directory=approval.working_directory,
        reason=approval.reason,
        status=approval.status
        if outcome is None
        else "consumed"
        if outcome.status == "completed"
        else approval.status,
        created_at=approval.created_at,
        expires_at=approval.expires_at,
        decided_at=approval.decided_at,
        decided_by=approval.decided_by,
        outcome=None if outcome is None else outcome.status,
        output=output,
    )
