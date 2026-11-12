"""Tests for the non-shell terminal policy and bounded process runner."""

import asyncio
import json
from pathlib import Path

from arch.agent.core import ToolRequest
from arch.tools.policy import HostCommand, PolicyDecision, TerminalToolPolicy
from arch.tools.terminal import TerminalExecutor


def _request(argv: list[str], cwd: str = ".", mode: str = "observe") -> ToolRequest:
    """Build one terminal request in the agent-facing JSON contract."""
    return ToolRequest(
        "terminal_exec",
        json.dumps({"argv": argv, "cwd": cwd, "access": "write" if mode == "mutate" else "read"}),
    )


def _command(command: str, cwd: str = ".", mode: str = "observe") -> ToolRequest:
    """Build the local-model-compatible direct command form."""
    return _request(command.split(), cwd, mode)


def _host_request(
    argv: list[str], cwd: str = ".", *, access: str = "read", capabilities: list[str] | None = None
) -> ToolRequest:
    return ToolRequest(
        "terminal_exec",
        json.dumps(
            {"argv": argv, "cwd": cwd, "access": access, "capabilities": capabilities or []}
        ),
    )


def test_generic_host_policy_requires_browser_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace)

    auto = policy.evaluate_host(_host_request(["pwd"], str(workspace)))
    outside = policy.evaluate_host(_host_request(["pwd"], "/"))
    write = policy.evaluate_host(
        _host_request(["touch", "report.txt"], str(workspace), access="write")
    )

    assert auto.decision is PolicyDecision.APPROVAL_REQUIRED
    assert outside.decision is PolicyDecision.APPROVAL_REQUIRED
    assert write.decision is PolicyDecision.APPROVAL_REQUIRED


def test_generic_host_policy_accepts_gpu_without_a_keyword_route(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace)

    result = policy.evaluate_host(
        _host_request(["nvidia-smi"], str(workspace), capabilities=["hardware"])
    )

    assert result.decision is PolicyDecision.APPROVAL_REQUIRED
    assert isinstance(result.command, HostCommand)


def test_host_policy_normalizes_the_local_model_home_placeholder_and_readonly_root(
    tmp_path: Path,
) -> None:
    """A real streamed tool call must not fail because `/home/user` is model boilerplate."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace)

    date = policy.evaluate_host(_host_request(["date"], "/home/user"))
    root = policy.evaluate_host(_host_request(["du", "-sh", "/"], "/"))

    assert date.decision is PolicyDecision.APPROVAL_REQUIRED
    assert date.command is not None
    assert date.command.working_directory == workspace.resolve()
    assert root.decision is PolicyDecision.APPROVAL_REQUIRED


def test_host_policy_normalizes_model_home_placeholders_in_find_arguments(tmp_path: Path) -> None:
    """The real approved `find /home/user` call must search this user's home instead."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace, home_root=tmp_path / "actual-home")
    (tmp_path / "actual-home").mkdir()

    result = policy.evaluate_host(
        _host_request(["find", "/home/user/Documents", "-type", "f", "-name", "*.pdf"])
    )

    assert result.decision is PolicyDecision.APPROVAL_REQUIRED
    assert result.command is not None
    assert result.command.argv[1] == str(tmp_path / "actual-home" / "Documents")


def test_terminal_policy_requires_approval_for_direct_commands(tmp_path: Path) -> None:
    """Every generic argv request uses the same reviewable authorization path."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace)

    uname = policy.evaluate(_request(["uname", "-a"], "/"))
    git = policy.evaluate(_request(["git", "status"], str(workspace)))
    disk = policy.evaluate(_request(["df", "-h"], "/"))
    memory = policy.evaluate(_request(["free", "-h"], "/"))
    fallback = policy.evaluate(_request(["ls"], "/model-invented-directory"))

    assert uname.decision is PolicyDecision.APPROVAL_REQUIRED
    assert uname.command is not None
    assert uname.command.working_directory == Path("/")
    assert git.decision is PolicyDecision.APPROVAL_REQUIRED
    assert git.command is not None
    assert disk.decision is PolicyDecision.APPROVAL_REQUIRED
    assert memory.decision is PolicyDecision.APPROVAL_REQUIRED
    assert fallback.decision is PolicyDecision.DENIED


def test_terminal_policy_accepts_direct_command_text_for_common_host_queries(
    tmp_path: Path,
) -> None:
    """The generic contract accepts only structured argv, never the retired command string."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace)

    results = [
        policy.evaluate(_command("uname -a", "/")),
        policy.evaluate(_command("free -h", "/")),
        policy.evaluate(_command("df -h", "/")),
        policy.evaluate(_command("ls")),
        policy.evaluate(ToolRequest("unknown_tool", '{"argv":"df -h"}')),
    ]

    assert all(result.decision is PolicyDecision.APPROVAL_REQUIRED for result in results[:4])
    assert results[4].decision is PolicyDecision.DENIED
    assert results[2].command is not None
    assert results[2].command.argv == ("df", "-h")


def test_terminal_policy_requires_approval_for_workspace_writes(tmp_path: Path) -> None:
    """Creating a file becomes a reviewable request before any side effect occurs."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace)

    result = policy.evaluate(_request(["touch", "report.txt"], mode="mutate"))

    assert result.decision is PolicyDecision.APPROVAL_REQUIRED
    assert result.command is not None
    assert not (workspace / "report.txt").exists()


def test_terminal_policy_rejects_shells_privilege_paths_and_invalid_direct_commands(
    tmp_path: Path,
) -> None:
    """The generic policy blocks privilege boundaries while leaving ordinary argv reviewable."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace)

    decisions = [
        policy.evaluate(_request(["bash", "-c", "id"])),
        policy.evaluate(_request(["python3", "-c", "print(1)"])),
        policy.evaluate(_request(["sudo", "id"], mode="mutate")),
        policy.evaluate(_request(["docker", "ps"], mode="mutate")),
        policy.evaluate(_request(["podman", "ps"], mode="mutate")),
        policy.evaluate(_request(["nsenter", "--target", "1"], mode="mutate")),
        policy.evaluate(_request(["pkexec", "id"], mode="mutate")),
        policy.evaluate(_request(["find", ".", "-exec", "id", ";"])),
        policy.evaluate(_request(["/bin/echo", "unsafe path"])),
    ]

    assert all(result.decision is PolicyDecision.DENIED for result in decisions)


def test_terminal_executor_rejects_unapproved_observations(
    tmp_path: Path,
) -> None:
    """The generic executor cannot run any command before browser approval."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace)
    command = policy.evaluate(_request(["pwd"])).command
    assert command is not None

    async def exercise() -> object:
        try:
            await TerminalExecutor(workspace, max_output_bytes=1024).execute_host(
                command, approved=False
            )
        except ValueError as error:
            return error
        return None

    assert isinstance(asyncio.run(exercise()), ValueError)


def test_terminal_executor_runs_an_approved_observation(tmp_path: Path) -> None:
    """An approved generic observation returns bounded process output."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace)
    command = policy.evaluate(_request(["date"])).command
    assert command is not None

    result = asyncio.run(TerminalExecutor(workspace).execute_host(command, approved=True))

    assert result.exit_code == 0
    assert result.stdout.strip()


def test_unapproved_host_write_is_rejected_before_execution(
    tmp_path: Path,
) -> None:
    """The executor requires gateway approval for a write request."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = TerminalToolPolicy(workspace).evaluate(
        _request(["touch", "blocked.txt"], mode="mutate")
    ).command
    assert command is not None

    async def exercise() -> object:
        try:
            await TerminalExecutor(workspace, timeout_seconds=10).execute_host(
                command, approved=False
            )
        except ValueError as error:
            return error
        return None

    assert isinstance(asyncio.run(exercise()), ValueError)
    assert not (workspace / "blocked.txt").exists()


def test_observation_request_has_no_side_effect_before_browser_approval(tmp_path: Path) -> None:
    """The failed sandbox proof keeps observe commands pending rather than executing them."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace)
    command = policy.evaluate(_request(["touch", "observation.txt"])).command
    assert command is not None

    decision = policy.evaluate(_request(["touch", "observation.txt"])).decision
    assert decision is PolicyDecision.APPROVAL_REQUIRED
    assert not (workspace / "observation.txt").exists()


def test_terminal_executor_rejects_mutations_without_browser_approval(
    tmp_path: Path,
) -> None:
    """A caller cannot bypass the gateway by calling the automatic executor for mutation mode."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = TerminalToolPolicy(workspace)
    command = policy.evaluate(_request(["touch", "report.txt"], mode="mutate")).command
    assert command is not None
    async def exercise() -> object:
        try:
            await TerminalExecutor(workspace).execute_host(command, approved=False)
        except ValueError as error:
            return error
        return None

    assert isinstance(asyncio.run(exercise()), ValueError)


def test_gateway_approved_mutation_executes_once_as_the_ordinary_user(
    tmp_path: Path,
) -> None:
    """An exact mutation executes after approval and cannot be replayed."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    from arch.memory.migrations import MigrationRunner
    from arch.tools.approvals import ApprovalStore
    from arch.tools.gateway import ToolGateway, ToolOutcome

    database_path = tmp_path / "voice.sqlite3"
    MigrationRunner(database_path).migrate()
    policy = TerminalToolPolicy(workspace)
    gateway = ToolGateway(
        policy, ApprovalStore(database_path), TerminalExecutor(workspace, 10.0, 4)
    )

    def approve_and_execute(request: ToolRequest) -> ToolOutcome:
        pending = asyncio.run(gateway.dispatch(request, session_id="session-1"))
        assert pending.approval_id is not None
        gateway._approvals.decide(pending.approval_id, "approved", "administrator")
        return asyncio.run(gateway.execute_approved(pending.approval_id, request, "session-1"))

    request = _host_request(["touch", "created.txt"], str(workspace), access="write")
    completed = approve_and_execute(request)

    assert completed.status == "completed"
    assert (workspace / "created.txt").is_file()
    assert asyncio.run(gateway.execute_approved("missing", request, "executor")).status == "denied"
