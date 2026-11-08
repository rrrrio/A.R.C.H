"""Tests for browser review and execution of one-time local tool approvals."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_voice_agent.agent.core import ToolRequest
from local_voice_agent.auth.credentials import provision_credentials
from local_voice_agent.auth.service import AuthService
from local_voice_agent.main import create_app
from local_voice_agent.memory.store import MemoryStore
from local_voice_agent.tools.approvals import ApprovalStore
from local_voice_agent.tools.gateway import ToolGateway
from local_voice_agent.tools.policy import TerminalToolPolicy
from local_voice_agent.tools.terminal import TerminalExecutor

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
