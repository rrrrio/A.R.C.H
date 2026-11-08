"""Parse and classify constrained terminal invocations before they can execute."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from local_voice_agent.agent.core import ToolRequest


class PolicyDecision(StrEnum):
    """The only execution dispositions available to a requested local tool."""

    AUTO = "auto"
    APPROVAL_REQUIRED = "approval_required"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class TerminalCommand:
    """A normalized, root-confined command that the terminal executor may accept."""

    argv: tuple[str, ...]
    working_directory: Path


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """One explainable policy outcome, optionally carrying a safe executable command."""

    decision: PolicyDecision
    reason: str
    command: TerminalCommand | None = None


class ToolPolicy(Protocol):
    """Classify an untrusted Agent Core request before any tool side effect."""

    def evaluate(self, request: ToolRequest) -> PolicyResult:
        """Return an explicit disposition without executing the request."""


class TerminalToolPolicy:
    """Admit only a small, inspectable terminal grammar within one workspace root."""

    _hard_denied = frozenset(
        {
            "bash",
            "bwrap",
            "chgrp",
            "chmod",
            "chown",
            "dd",
            "env",
            "fish",
            "halt",
            "mkfs",
            "mount",
            "node",
            "perl",
            "php",
            "poweroff",
            "python",
            "python3",
            "reboot",
            "rm",
            "ruby",
            "sh",
            "shutdown",
            "sudo",
            "su",
            "tee",
            "umount",
            "useradd",
            "usermod",
            "zsh",
        }
    )

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root.resolve()

    def evaluate(self, request: ToolRequest) -> PolicyResult:
        """Parse an untrusted request into a decision without allowing syntax bypasses."""
        if request.name != "terminal":
            return PolicyResult(PolicyDecision.DENIED, "Unsupported tool name.")
        parsed = _parse_terminal_arguments(request.arguments_json)
        if parsed is None:
            return PolicyResult(
                PolicyDecision.DENIED, "Terminal arguments must contain argv and cwd."
            )
        argv, requested_cwd = parsed
        command_name, *arguments = argv
        if command_name in self._hard_denied:
            return PolicyResult(PolicyDecision.DENIED, "This command is permanently blocked.")
        working_directory = self._resolve_workspace_path(requested_cwd)
        if working_directory is None or not working_directory.is_dir():
            return PolicyResult(
                PolicyDecision.DENIED, "Working directory is outside the terminal workspace."
            )
        if command_name == "pwd" and not arguments:
            return PolicyResult(
                PolicyDecision.AUTO,
                "pwd reads the confined working directory.",
                TerminalCommand(argv, working_directory),
            )
        if command_name == "ls" and all(
            argument in {"-a", "-l", "-al", "-la"} for argument in arguments
        ):
            return PolicyResult(
                PolicyDecision.AUTO,
                "ls only lists the confined working directory.",
                TerminalCommand(argv, working_directory),
            )
        if command_name in {"touch", "cat"} and arguments and all(
            _is_confined_operand(argument, self._resolve_workspace_path) for argument in arguments
        ):
            return PolicyResult(
                PolicyDecision.APPROVAL_REQUIRED,
                f"{command_name} accesses workspace files.",
                TerminalCommand(argv, working_directory),
            )
        if command_name == "mkdir" and _mkdir_paths_are_confined(
            arguments, self._resolve_workspace_path
        ):
            return PolicyResult(
                PolicyDecision.APPROVAL_REQUIRED,
                "mkdir creates workspace directories.",
                TerminalCommand(argv, working_directory),
            )
        if command_name not in {"pwd", "ls", "touch", "cat", "mkdir"}:
            return PolicyResult(
                PolicyDecision.APPROVAL_REQUIRED,
                "This command requires approval and a future constrained command adapter.",
            )
        return PolicyResult(
            PolicyDecision.DENIED, "Command arguments are outside the allowed terminal grammar."
        )

    def _resolve_workspace_path(self, raw_path: str) -> Path | None:
        """Resolve an existing or future relative path while rejecting all root escapes."""
        candidate = Path(raw_path)
        if candidate.is_absolute():
            return None
        resolved = (self._workspace_root / candidate).resolve()
        if resolved == self._workspace_root or self._workspace_root in resolved.parents:
            return resolved
        return None


def _parse_terminal_arguments(arguments_json: str) -> tuple[tuple[str, ...], str] | None:
    """Accept one bounded object shape and reject scalar, shell, and malformed inputs."""
    try:
        value = json.loads(arguments_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or set(value) - {"argv", "cwd"}:
        return None
    argv = value.get("argv")
    cwd = value.get("cwd", ".")
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= 16
        or not all(
            isinstance(item, str) and item and len(item) <= 256 and "\x00" not in item
            for item in argv
        )
        or not isinstance(cwd, str)
        or not cwd
        or len(cwd) > 256
        or "\x00" in cwd
    ):
        return None
    return tuple(argv), cwd


def _mkdir_paths_are_confined(
    arguments: list[str], resolve_path: Callable[[str], Path | None]
) -> bool:
    """Allow mkdir's sole supported flag only when every operand stays in the workspace."""
    paths = arguments[1:] if arguments[:1] == ["-p"] else arguments
    return bool(paths) and all(_is_confined_operand(path, resolve_path) for path in paths)


def _is_confined_operand(raw_path: str, resolve_path: Callable[[str], Path | None]) -> bool:
    """Reject program options so only a path operand can reach a filesystem command."""
    return not raw_path.startswith("-") and resolve_path(raw_path) is not None
