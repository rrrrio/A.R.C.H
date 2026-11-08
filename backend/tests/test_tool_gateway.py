"""Tests for constrained local tool dispatch and one-time approval execution."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from local_voice_agent.agent.core import ToolRequest
from local_voice_agent.memory.migrations import MigrationRunner
from local_voice_agent.tools.approvals import ApprovalStore
from local_voice_agent.tools.gateway import ToolGateway
from local_voice_agent.tools.policy import TerminalToolPolicy
from local_voice_agent.tools.terminal import TerminalExecutor


def _request(argv: list[str], cwd: str = ".") -> ToolRequest:
    return ToolRequest("terminal", json.dumps({"argv": argv, "cwd": cwd}))


def _gateway(tmp_path: Path) -> ToolGateway:
    database_path = tmp_path / "voice.sqlite3"
    MigrationRunner(database_path).migrate()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return ToolGateway(
        TerminalToolPolicy(workspace), ApprovalStore(database_path), TerminalExecutor(workspace)
    )


def test_gateway_runs_only_automatic_readonly_commands_without_approval(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)

    outcome = asyncio.run(gateway.dispatch(_request(["pwd"])))

    assert outcome.status == "completed"
    assert outcome.result is not None
    assert outcome.result.stdout.strip() == str((tmp_path / "workspace").resolve())


def test_gateway_approval_executes_one_confined_write_exactly_once(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    request = _request(["mkdir", "reports"])

    pending = asyncio.run(gateway.dispatch(request))

    assert pending.status == "approval_required"
    assert pending.approval_id is not None
    assert not (tmp_path / "workspace" / "reports").exists()
    assert (
        gateway._approvals.decide(pending.approval_id, "approved", "administrator").status
        == "approved"
    )
    completed = asyncio.run(gateway.execute_approved(pending.approval_id, request, "executor"))

    assert completed.status == "completed"
    assert (tmp_path / "workspace" / "reports").is_dir()
    assert (
        asyncio.run(gateway.execute_approved(pending.approval_id, request, "executor")).status
        == "denied"
    )


def test_gateway_never_persists_or_executes_hard_denied_requests(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)

    outcome = asyncio.run(gateway.dispatch(_request(["rm", "-rf", "."])))

    assert outcome.status == "denied"
