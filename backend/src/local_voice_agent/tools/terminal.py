"""Direct argv execution for the small terminal grammar admitted by ToolPolicy."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from local_voice_agent.tools.policy import TerminalCommand


@dataclass(frozen=True, slots=True)
class TerminalResult:
    """Bounded process output suitable for audit and one local agent response."""

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    output_truncated: bool


class TerminalExecutor:
    """Run only second-validated read-only commands through an anchored workspace descriptor."""

    _executables = {
        "pwd": "/usr/bin/pwd",
        "ls": "/usr/bin/ls",
    }

    def __init__(
        self,
        workspace_root: Path,
        timeout_seconds: float = 10.0,
        max_output_bytes: int = 16 * 1024,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Terminal timeout must be positive.")
        if max_output_bytes < 1:
            raise ValueError("Terminal output limit must be positive.")
        self._workspace_root = workspace_root.resolve()
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes

    async def execute(self, command: TerminalCommand) -> TerminalResult:
        """Revalidate one command and bind the child to a no-follow workspace descriptor."""
        executable = _validated_executable(command.argv)
        working_directory_fd = _open_workspace_directory(
            self._workspace_root, command.working_directory
        )
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                *command.argv[1:],
                cwd=f"/proc/self/fd/{working_directory_fd}",
                env=_terminal_environment(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                pass_fds=(working_directory_fd,),
            )
        except BaseException:
            os.close(working_directory_fd)
            raise
        try:
            assert process.stdout is not None
            assert process.stderr is not None
            stdout_task = asyncio.create_task(_read_limited(process.stdout, self._max_output_bytes))
            stderr_task = asyncio.create_task(_read_limited(process.stderr, self._max_output_bytes))
            timed_out = False
            try:
                await asyncio.wait_for(process.wait(), timeout=self._timeout_seconds)
            except TimeoutError:
                timed_out = True
                process.kill()
                await process.wait()
            stdout, stdout_truncated = await stdout_task
            stderr, stderr_truncated = await stderr_task
            return TerminalResult(
                exit_code=None if timed_out else process.returncode,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                timed_out=timed_out,
                output_truncated=stdout_truncated or stderr_truncated,
            )
        finally:
            os.close(working_directory_fd)


def _validated_executable(argv: tuple[str, ...]) -> str:
    """Reject direct callers that forge a command absent from the read-only executor grammar."""
    command, *arguments = argv
    if command == "pwd" and not arguments:
        return "/usr/bin/pwd"
    if command == "ls" and all(argument in {"-a", "-l", "-al", "-la"} for argument in arguments):
        return "/usr/bin/ls"
    raise ValueError("Terminal command has no safe executor.")


def _open_workspace_directory(workspace_root: Path, working_directory: Path) -> int:
    """Open a confined directory so a later symlink swap cannot redirect the child process."""
    try:
        relative = working_directory.relative_to(workspace_root)
    except ValueError as error:
        raise ValueError("Terminal working directory is outside the workspace.") from error
    root_fd = os.open(workspace_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    current_fd = os.dup(root_fd)
    os.close(root_fd)
    try:
        for component in relative.parts:
            if component in {"", ".", ".."}:
                raise ValueError("Terminal working directory contains an invalid component.")
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _terminal_environment() -> dict[str, str]:
    """Expose locale only; commands use fixed absolute executables and inherit no secrets."""
    return {
        key: value
        for key in ("LANG", "LC_ALL")
        if (value := os.environ.get(key)) is not None
    }


async def _read_limited(stream: asyncio.StreamReader, limit: int) -> tuple[bytes, bool]:
    """Drain a process stream while retaining only a bounded prefix for the caller."""
    collected = bytearray()
    truncated = False
    while chunk := await stream.read(4096):
        available = limit - len(collected)
        if available > 0:
            collected.extend(chunk[:available])
        if len(chunk) > available:
            truncated = True
    return bytes(collected), truncated
