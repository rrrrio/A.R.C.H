"""Direct argv execution for the small terminal grammar admitted by ToolPolicy."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from arch.tools.policy import TerminalCommand


@dataclass(frozen=True, slots=True)
class TerminalResult:
    """Bounded process output suitable for audit and one local agent response."""

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    output_truncated: bool


class TerminalExecutor:
    """Run second-validated commands through anchored workspace descriptors."""

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
        """Execute only automatic read-only commands through a no-follow directory descriptor."""
        executable = _validated_readonly_executable(command.argv)
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
            except asyncio.CancelledError:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                stdout_task.cancel()
                stderr_task.cancel()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                raise
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

    async def _execute_approved(self, command: TerminalCommand) -> TerminalResult:
        """Run a Gateway-owned approved adapter without shell parsing or broad filesystem access."""
        return await asyncio.to_thread(self._execute_approved_sync, command)

    def _execute_approved_sync(self, command: TerminalCommand) -> TerminalResult:
        _validated_approved_command(command.argv)
        command_name, *arguments = command.argv
        if command_name == "touch":
            for raw_path in arguments:
                parent_fd, leaf = _open_parent_directory(self._workspace_root, raw_path)
                try:
                    fd = os.open(
                        leaf, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd
                    )
                    os.close(fd)
                finally:
                    os.close(parent_fd)
            return TerminalResult(0, "", "", False, False)
        if command_name == "mkdir":
            create_parents = arguments[:1] == ["-p"]
            for raw_path in arguments[int(create_parents) :]:
                _mkdir_confined(self._workspace_root, raw_path, create_parents)
            return TerminalResult(0, "", "", False, False)
        if command_name == "cat":
            return _read_confined_files(self._workspace_root, arguments, self._max_output_bytes)
        raise ValueError("Terminal command has no approved executor.")


def _validated_readonly_executable(argv: tuple[str, ...]) -> str:
    """Reject direct callers that forge a command absent from the read-only executor grammar."""
    command, *arguments = argv
    if command == "pwd" and not arguments:
        return "/usr/bin/pwd"
    if command == "ls" and all(argument in {"-a", "-l", "-al", "-la"} for argument in arguments):
        return "/usr/bin/ls"
    raise ValueError("Terminal command has no safe executor.")


def _validated_approved_command(argv: tuple[str, ...]) -> None:
    """Limit approved commands to adapters that never delegate to a shell or interpreter."""
    command, *arguments = argv
    if command in {"touch", "cat"} and arguments and all(_path_operand(path) for path in arguments):
        return
    paths = arguments[1:] if command == "mkdir" and arguments[:1] == ["-p"] else arguments
    if command == "mkdir" and paths and all(_path_operand(path) for path in paths):
        return
    raise ValueError("Terminal command has no approved executor.")


def _path_operand(raw_path: str) -> bool:
    """Keep adapter paths relative and lexical so each component can be opened no-follow."""
    path = Path(raw_path)
    return (
        not raw_path.startswith("-")
        and not path.is_absolute()
        and all(component not in {"", ".", ".."} for component in path.parts)
    )


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


def _open_parent_directory(workspace_root: Path, raw_path: str) -> tuple[int, str]:
    """Open a relative path's parent without following symlinks in directory components."""
    if not _path_operand(raw_path):
        raise ValueError("Terminal path is not a confined operand.")
    parts = Path(raw_path).parts
    leaf = parts[-1]
    current_fd = os.open(workspace_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, leaf
    except BaseException:
        os.close(current_fd)
        raise


def _mkdir_confined(workspace_root: Path, raw_path: str, create_parents: bool) -> None:
    """Create each component while refusing symlink traversal and existing final directories."""
    if not _path_operand(raw_path):
        raise ValueError("Terminal path is not a confined operand.")
    parts = Path(raw_path).parts
    current_fd = os.open(workspace_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, component in enumerate(parts):
            try:
                os.mkdir(component, mode=0o700, dir_fd=current_fd)
            except FileExistsError:
                if index == len(parts) - 1 and not create_parents:
                    raise
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = next_fd
    finally:
        os.close(current_fd)


def _read_confined_files(
    workspace_root: Path, raw_paths: list[str], max_output_bytes: int
) -> TerminalResult:
    """Read approved regular files through no-follow descriptors and retain a bounded prefix."""
    chunks: list[bytes] = []
    remaining = max_output_bytes
    truncated = False
    for raw_path in raw_paths:
        parent_fd, leaf = _open_parent_directory(workspace_root, raw_path)
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        try:
            while chunk := os.read(fd, min(4096, remaining + 1)):
                if len(chunk) > remaining:
                    chunks.append(chunk[:remaining])
                    truncated = True
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
                if remaining == 0:
                    if os.read(fd, 1):
                        truncated = True
                    break
        finally:
            os.close(fd)
        if truncated:
            break
    return TerminalResult(
        0,
        b"".join(chunks).decode("utf-8", errors="replace"),
        "",
        False,
        truncated,
    )


def _terminal_environment() -> dict[str, str]:
    """Expose locale only; commands use fixed absolute executables and inherit no secrets."""
    return {key: value for key in ("LANG", "LC_ALL") if (value := os.environ.get(key)) is not None}


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
