"""Tests for constrained local tool dispatch and one-time approval execution."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from pathlib import Path

import httpx

from arch.agent.core import ToolRequest
from arch.memory.migrations import MigrationRunner
from arch.tools.approvals import ApprovalStore, ToolApproval
from arch.tools.gateway import ToolGateway, ToolOutcome
from arch.tools.policy import TerminalToolPolicy, ToolPolicy
from arch.tools.search import SearxngSearchClient
from arch.tools.terminal import TerminalExecutor


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


def test_gateway_rejects_an_approved_request_retracted_before_consumption(tmp_path: Path) -> None:
    """Session cleanup wins when it locks and retracts an approved request before execution."""
    gateway = _gateway(tmp_path)
    request = _request(["touch", "report.txt"])
    pending = asyncio.run(gateway.dispatch(request))
    approval_id = pending.approval_id
    assert approval_id is not None
    approved = gateway._approvals.decide(approval_id, "approved", "administrator")
    assert approved.status == "approved"

    async def exercise() -> ToolOutcome:
        cancellation = asyncio.create_task(gateway.cancel_pending(approval_id))
        await asyncio.sleep(0)
        outcome = await gateway.execute_approved(approval_id, request, "executor")
        await cancellation
        return outcome

    outcome = asyncio.run(exercise())

    assert outcome.status == "denied"
    assert not (tmp_path / "workspace" / "report.txt").exists()


def test_gateway_never_persists_or_executes_hard_denied_requests(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)

    outcome = asyncio.run(gateway.dispatch(_request(["rm", "-rf", "."])))

    assert outcome.status == "denied"


def test_gateway_requires_explicit_search_enablement_and_returns_bounded_results(
    tmp_path: Path,
) -> None:
    """Search stays off unless a loopback client is injected into the application gateway."""
    request = ToolRequest("web_search", '{"query":"local voice agent"}')
    disabled = _gateway(tmp_path)
    assert asyncio.run(disabled.dispatch(request)).message == "Local web search is disabled."

    database_path = tmp_path / "enabled.sqlite3"
    MigrationRunner(database_path).migrate()
    workspace = tmp_path / "enabled-workspace"
    workspace.mkdir()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "本机", "url": "https://example.test", "content": "摘要"}
                ]
            },
        )

    client = SearxngSearchClient(
        "http://127.0.0.1:8080", transport=httpx.MockTransport(handler)
    )
    enabled = ToolGateway(
        TerminalToolPolicy(workspace),
        ApprovalStore(database_path),
        TerminalExecutor(workspace),
        search_client=client,
    )

    outcome = asyncio.run(enabled.dispatch(request))

    assert outcome.status == "completed"
    assert [(item.title, item.url) for item in outcome.search_results] == [
        ("本机", "https://example.test")
    ]


class BlockingApprovalStore(ApprovalStore):
    """Delay a real SQLite insert until the caller has requested cancellation."""

    def __init__(self, database_path: Path) -> None:
        super().__init__(database_path)
        self.started = threading.Event()
        self.release = threading.Event()

    def create(
        self, request: ToolRequest, policy: ToolPolicy, ttl_seconds: int = 120
    ) -> ToolApproval:
        self.started.set()
        assert self.release.wait(timeout=2)
        return super().create(request, policy, ttl_seconds)


def test_gateway_retracts_an_approval_created_after_media_cancellation(tmp_path: Path) -> None:
    """A cancelled voice turn cannot leave a later browser-approvable action behind."""
    database_path = tmp_path / "voice.sqlite3"
    MigrationRunner(database_path).migrate()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    approvals = BlockingApprovalStore(database_path)
    gateway = ToolGateway(TerminalToolPolicy(workspace), approvals, TerminalExecutor(workspace))

    async def exercise() -> None:
        dispatch_task = asyncio.create_task(gateway.dispatch(_request(["touch", "report.txt"])))
        await asyncio.to_thread(approvals.started.wait)
        dispatch_task.cancel()
        approvals.release.set()
        try:
            await dispatch_task
        except asyncio.CancelledError:
            pass

    asyncio.run(exercise())

    assert approvals.list_pending() == ()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT status, decided_by FROM tool_approvals").fetchall() == [
            ("rejected", "interrupted_media")
        ]
        assert connection.execute(
            "SELECT action, actor FROM tool_approval_events ORDER BY id"
        ).fetchall() == [("created", "system"), ("rejected", "interrupted_media")]
