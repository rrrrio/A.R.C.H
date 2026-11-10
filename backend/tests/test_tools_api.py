"""Tests for browser review and execution of one-time local tool approvals."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from arch.agent.core import ToolRequest
from arch.auth.credentials import provision_credentials
from arch.auth.service import AuthService
from arch.main import create_app
from arch.memory.store import MemoryStore
from arch.tools.approvals import ApprovalStore
from arch.tools.gateway import ToolGateway, ToolOutcome
from arch.tools.policy import TerminalToolPolicy
from arch.tools.terminal import TerminalExecutor
from arch.voice.webrtc import WebRTCSignalingService

PUBLIC_ORIGIN = "https://voice.test:8443"


def _request(argv: list[str]) -> ToolRequest:
    return ToolRequest("terminal", json.dumps({"argv": argv, "cwd": "."}))


def _application(tmp_path: Path) -> tuple[FastAPI, ToolGateway, Path]:
    credential_path = tmp_path / "admin.json"
    provision_credentials(credential_path, "correct-horse-battery-staple")
    database_path = tmp_path / "voice.sqlite3"
    memory_store = MemoryStore(database_path)
    memory_store.migrate()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    approvals = ApprovalStore(database_path)
    gateway = ToolGateway(TerminalToolPolicy(workspace), approvals, TerminalExecutor(workspace))
    return (
        create_app(
            AuthService(credential_path, PUBLIC_ORIGIN),
            memory_store=memory_store,
            approval_store=approvals,
            tool_gateway=gateway,
        ),
        gateway,
        workspace,
    )


class FakeApprovalContinuation:
    """Record the already-executed outcome passed from the browser approval endpoint."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ToolOutcome]] = []

    async def continue_after_approved_tool(self, approval_id: str, outcome: ToolOutcome) -> bool:
        self.calls.append((approval_id, outcome))
        return True


def test_tool_approval_api_requires_login_and_csrf_then_executes_exactly_once(
    tmp_path: Path,
) -> None:
    app, gateway, workspace = _application(tmp_path)
    request = _request(["touch", "report.txt"])
    pending = asyncio.run(gateway.dispatch(request))
    assert pending.approval_id is not None

    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        anonymous = client.get("/api/tools/approvals")
        login = client.post(
            "/api/auth/login",
            headers={"Origin": PUBLIC_ORIGIN},
            json={"password": "correct-horse-battery-staple"},
        )
        csrf_token = login.json()["csrf_token"]
        listed = client.get("/api/tools/approvals")
        missing_csrf = client.patch(
            f"/api/tools/approvals/{pending.approval_id}",
            headers={"Origin": PUBLIC_ORIGIN},
            json={"action": "approved"},
        )
        approved = client.patch(
            f"/api/tools/approvals/{pending.approval_id}",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf_token},
            json={"action": "approved"},
        )
        repeated = client.patch(
            f"/api/tools/approvals/{pending.approval_id}",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf_token},
            json={"action": "approved"},
        )

    assert anonymous.status_code == 401
    assert listed.json()[0]["id"] == pending.approval_id
    assert listed.json()[0]["argv_json"] == '["touch","report.txt"]'
    assert missing_csrf.status_code == 403
    assert approved.json()["status"] == "consumed"
    assert approved.json()["outcome"] == "completed"
    assert (workspace / "report.txt").is_file()
    assert repeated.status_code == 409


def test_tool_approval_api_rejection_has_no_side_effect(tmp_path: Path) -> None:
    app, gateway, workspace = _application(tmp_path)
    pending = asyncio.run(gateway.dispatch(_request(["mkdir", "reports"])))
    assert pending.approval_id is not None

    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        login = client.post(
            "/api/auth/login",
            headers={"Origin": PUBLIC_ORIGIN},
            json={"password": "correct-horse-battery-staple"},
        )
        rejected = client.patch(
            f"/api/tools/approvals/{pending.approval_id}",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]},
            json={"action": "rejected"},
        )

    assert rejected.json()["status"] == "rejected"
    assert not (workspace / "reports").exists()


def test_tool_approval_api_forwards_only_consumed_execution_to_active_voice_session(
    tmp_path: Path,
) -> None:
    """The authenticated approval endpoint forwards its result through the session boundary."""
    app, gateway, _ = _application(tmp_path)
    continuation = FakeApprovalContinuation()
    app.state.voice_session = continuation
    app.state.signaling_service = WebRTCSignalingService()
    pending = asyncio.run(gateway.dispatch(_request(["touch", "report.txt"])))
    assert pending.approval_id is not None

    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        login = client.post(
            "/api/auth/login",
            headers={"Origin": PUBLIC_ORIGIN},
            json={"password": "correct-horse-battery-staple"},
        )
        approved = client.patch(
            f"/api/tools/approvals/{pending.approval_id}",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]},
            json={"action": "approved"},
        )

    assert approved.status_code == 200
    assert [(approval_id, outcome.status) for approval_id, outcome in continuation.calls] == [
        (pending.approval_id, "completed")
    ]
