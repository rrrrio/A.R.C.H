"""Execute policy-normalized host commands with bounded output."""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from arch.tools.policy import HostCommand


@dataclass(frozen=True, slots=True)
class TerminalResult:
    """Bounded process output suitable for audit and one local agent response."""

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    output_truncated: bool


class TerminalExecutor:
    """Run policy-built direct commands with a sandbox or a one-time user approval."""

    _path = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
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

    async def execute_host(self, command: HostCommand, *, approved: bool) -> TerminalResult:
        """Run a generic Agent command through the same bounded process boundary."""
        if not approved:
            raise ValueError("Generic host commands require an active authorization.")
        return await self._run_host(
            command,
            sandboxed=command.access == "read" and not {"service", "docker"} & command.capabilities,
            network="network" in command.capabilities,
        )

    async def _run_host(
        self, command: HostCommand, *, sandboxed: bool, network: bool
    ) -> TerminalResult:
        """Spawn generic argv or a fixed bash interpreter without model-controlled executables."""
        if command.kind == "exec":
            executable = _resolve_executable(command.argv[0], self._path)
            child_argv = [executable, *command.argv[1:]]
        else:
            child_argv = [
                "/bin/bash", "--noprofile", "--norc", "-o", "pipefail", "-c", command.script or ""
            ]
        properties = [
            "NoNewPrivileges=yes",
            "KillMode=control-group",
            f"RuntimeMaxSec={self._timeout_seconds}s",
        ]
        if sandboxed:
            properties.extend(
                (
                    "ProtectSystem=strict",
                    "ProtectHome=tmpfs",
                    "PrivateTmp=yes",
                    "PrivateNetwork=no" if network else "PrivateNetwork=yes",
                    "DevicePolicy=closed",
                    "DeviceAllow=/dev/null rwm",
                    "ProtectKernelTunables=yes",
                    "ProtectControlGroups=yes",
                    "RestrictSUIDSGID=yes",
                    "RestrictNamespaces=yes",
                    "LockPersonality=yes",
                    "RestrictRealtime=yes",
                    f"ReadOnlyPaths={command.working_directory}",
                )
            )
            if "hardware" in command.capabilities:
                properties.extend(
                    (
                        "DeviceAllow=/dev/nvidiactl rwm",
                        "DeviceAllow=/dev/nvidia-uvm rwm",
                        "DeviceAllow=/dev/nvidia0 rwm",
                    )
                )
        argv = [
            "/usr/bin/systemd-run",
            "--user",
            "--pipe",
            "--quiet",
            "--collect",
            f"--working-directory={command.working_directory}",
            *(f"--property={property}" for property in properties),
            *child_argv,
        ]
        return await self._spawn(argv)

    async def _spawn(self, argv: list[str]) -> TerminalResult:
        """Collect bounded output and kill the supervisor when cancellation or timeout wins."""
        process = await asyncio.create_subprocess_exec(
            *argv,
            env=_terminal_environment(self._path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task = asyncio.create_task(_read_limited(process.stdout, self._max_output_bytes))
        stderr_task = asyncio.create_task(_read_limited(process.stderr, self._max_output_bytes))
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=self._timeout_seconds + 1)
        except TimeoutError:
            timed_out = True
            process.kill()
            await process.wait()
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        stdout, stdout_truncated = await stdout_task
        stderr, stderr_truncated = await stderr_task
        return TerminalResult(
            None if timed_out else process.returncode,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            timed_out,
            stdout_truncated or stderr_truncated,
        )


def _resolve_executable(command: str, path: str) -> str:
    """Resolve a bare command through a fixed system PATH, never a model-provided path."""
    executable = shutil.which(command, path=path)
    if executable is None:
        raise ValueError("Terminal command is not available on the service PATH.")
    return executable




def _terminal_environment(path: str) -> dict[str, str]:
    """Expose a minimal stable environment to transient user-systemd units."""
    environment = {"PATH": path}
    for key in ("LANG", "LC_ALL", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"):
        value = os.environ.get(key)
        if value is not None:
            environment[key] = value
    return environment


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
