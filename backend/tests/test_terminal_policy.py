"""Tests for the non-shell terminal policy and bounded process runner."""

import asyncio
import json
from pathlib import Path

from local_voice_agent.agent.core import ToolRequest
from local_voice_agent.tools.policy import PolicyDecision, TerminalToolPolicy
from local_voice_agent.tools.terminal import TerminalExecutor


def _request(argv: list[str], cwd: str = ".") -> ToolRequest:
    """Build one terminal request in the agent-facing JSON contract."""
    return ToolRequest("terminal", json.dumps({"argv": argv, "cwd": cwd}))


def test_terminal_policy_auto_approves_only_pwd_and_workspace_listing(tmp_path: Path) -> None:
    """Read-only root inspection does not require an approval round trip."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace)

    pwd = policy.evaluate(_request(["pwd"]))
    listing = policy.evaluate(_request(["ls", "-la"]))

    assert pwd.decision is PolicyDecision.AUTO
    assert listing.decision is PolicyDecision.AUTO
    assert pwd.command is not None
    assert pwd.command.working_directory == workspace.resolve()


def test_terminal_policy_requires_approval_for_workspace_writes(tmp_path: Path) -> None:
    """Creating a file becomes a reviewable request before any side effect occurs."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace)

    result = policy.evaluate(_request(["touch", "report.txt"]))

    assert result.decision is PolicyDecision.APPROVAL_REQUIRED
    assert result.command is not None
    assert not (workspace / "report.txt").exists()


def test_terminal_policy_rejects_shells_hard_denied_commands_and_root_escapes(
    tmp_path: Path,
) -> None:
    """Approval cannot turn shell, deletion, interpreter, or path traversal into execution."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (workspace / "escape").symlink_to(outside)
    policy = TerminalToolPolicy(workspace)

    decisions = [
        policy.evaluate(_request(["bash", "-c", "id"])),
        policy.evaluate(_request(["rm", "-rf", "."])),
        policy.evaluate(_request(["python3", "-c", "print(1)"])),
        policy.evaluate(_request(["touch", "../outside.txt"])),
        policy.evaluate(_request(["touch", "--reference=/etc/passwd", "report.txt"])),
        policy.evaluate(_request(["cat", "--", "escape"])),
        policy.evaluate(_request(["cat", "escape"])),
    ]

    assert all(result.decision is PolicyDecision.DENIED for result in decisions)


def test_terminal_executor_runs_only_policy_built_readonly_commands(
    tmp_path: Path,
) -> None:
    """The executor uses a fixed binary and follows a descriptor-anchored workspace only."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace)
    command = policy.evaluate(_request(["pwd"])).command
    assert command is not None

    result = asyncio.run(TerminalExecutor(workspace, max_output_bytes=1024).execute(command))

    assert result.exit_code == 0
    assert result.stdout.strip() == str(workspace.resolve())
    assert not result.output_truncated


def test_terminal_executor_rejects_forged_commands_and_symlinked_working_directories(
    tmp_path: Path,
) -> None:
    """Direct callers cannot expand the executor grammar or replace a checked directory with a link.

    The descriptor path stays confined even when the lexical workspace path becomes a symlink.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    policy = TerminalToolPolicy(workspace)
    command = policy.evaluate(_request(["pwd"])).command
    assert command is not None
    (workspace / "nested").symlink_to(outside, target_is_directory=True)

    async def exercise() -> tuple[object, object]:
        executor = TerminalExecutor(workspace)
        forged = asyncio.create_task(
            executor.execute(command.__class__(("cat", "/etc/passwd"), workspace))
        )
        linked = asyncio.create_task(
            executor.execute(command.__class__(("pwd",), workspace / "nested"))
        )
        return await asyncio.gather(forged, linked, return_exceptions=True)

    forged, linked = asyncio.run(exercise())

    assert isinstance(forged, ValueError)
    assert isinstance(linked, OSError)


def test_gateway_approved_adapters_create_and_read_confined_files_without_following_symlinks(
    tmp_path: Path,
) -> None:
    """Approval does not permit a write or read adapter to traverse a replaced workspace path."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (workspace / "escape.txt").symlink_to(outside)
    from local_voice_agent.memory.migrations import MigrationRunner
    from local_voice_agent.tools.approvals import ApprovalStore
    from local_voice_agent.tools.gateway import ToolGateway, ToolOutcome

    database_path = tmp_path / "voice.sqlite3"
    MigrationRunner(database_path).migrate()
    policy = TerminalToolPolicy(workspace)
    gateway = ToolGateway(
        policy, ApprovalStore(database_path), TerminalExecutor(workspace, 10.0, 4)
    )

    def approve_and_execute(request: ToolRequest) -> ToolOutcome:
        pending = asyncio.run(gateway.dispatch(request))
        assert pending.approval_id is not None
        gateway._approvals.decide(pending.approval_id, "approved", "administrator")
        return asyncio.run(gateway.execute_approved(pending.approval_id, request, "executor"))

    approve_and_execute(_request(["touch", "created.txt"]))
    (workspace / "created.txt").write_text("abcdef")
    read = approve_and_execute(_request(["cat", "created.txt"]))
    approve_and_execute(_request(["mkdir", "nested"]))

    assert read.result is not None
    assert read.result.stdout == "abcd"
    assert read.result.output_truncated
    assert (workspace / "nested").is_dir()
    denied = asyncio.run(gateway.dispatch(_request(["cat", "escape.txt"])))
    assert denied.status == "denied"
    assert outside.read_text() == "private"
