"""Classify direct, non-root terminal invocations before they can execute."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from arch.agent.core import ToolRequest


class PolicyDecision(StrEnum):
    """The only execution dispositions available to a requested local tool."""

    AUTO = "auto"
    APPROVAL_REQUIRED = "approval_required"
    DENIED = "denied"


TerminalProfile = Literal["observe", "assist", "operator"]
TerminalAccess = Literal["read", "write"]


@dataclass(frozen=True, slots=True)
class HostCommand:
    """A generic exec or shell request normalized by trusted code."""

    kind: Literal["exec", "shell"]
    argv: tuple[str, ...]
    script: str | None
    working_directory: Path
    access: TerminalAccess
    capabilities: frozenset[str]
    home_root: Path


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """One explainable policy outcome, optionally carrying a safe executable command."""

    decision: PolicyDecision
    reason: str
    command: HostCommand | None = None


class TerminalToolPolicy:
    """Allow direct non-root commands while blocking composition and privilege boundaries."""

    _hard_denied = frozenset(
        {
            "bash",
            "bwrap",
            "capsh",
            "chroot",
            "dash",
            "docker",
            "doas",
            "ed",
            "env",
            "fish",
            "findmnt",
            "awk",
            "less",
            "mount",
            "nano",
            "node",
            "nsenter",
            "perl",
            "php",
            "pkexec",
            "podman",
            "python",
            "python3",
            "ruby",
            "sed",
            "sh",
            "sudo",
            "su",
            "systemd-run",
            "unshare",
            "umount",
            "vi",
            "vim",
            "zsh",
            "xargs",
        }
    )
    _host_capabilities = frozenset({"network", "hardware", "service", "docker"})

    def __init__(
        self,
        workspace_root: Path,
        home_root: Path | None = None,
    ) -> None:
        self._workspace_root = workspace_root.resolve()
        self._home_root = (home_root or Path.home()).resolve()

    def evaluate(self, request: ToolRequest) -> PolicyResult:
        """Evaluate one supported generic host tool request."""
        if request.name not in {"terminal_exec", "terminal_shell"}:
            return PolicyResult(PolicyDecision.DENIED, "Unsupported tool name.")
        return self.evaluate_host(request)

    def evaluate_host(
        self, request: ToolRequest, profile: TerminalProfile = "assist"
    ) -> PolicyResult:
        """Validate generic host calls and auto-run only a confined read-only argv request."""
        if profile not in ("observe", "assist", "operator"):
            return PolicyResult(PolicyDecision.DENIED, "终端权限档位无效。")
        command = self._parse_host_command(request)
        if command is None:
            return PolicyResult(PolicyDecision.DENIED, "终端工具参数无效。")
        if (
            command.kind == "exec"
            and command.argv[0] in self._hard_denied
            and not (command.argv[0] == "docker" and "docker" in command.capabilities)
        ):
            return PolicyResult(PolicyDecision.DENIED, "该命令不能绕过主机权限边界。")
        if command.kind == "shell" and profile == "observe":
            return PolicyResult(PolicyDecision.DENIED, "观察档位不允许 shell。", command)
        if profile == "observe" and (command.access != "read" or command.capabilities):
            return PolicyResult(PolicyDecision.DENIED, "观察档位只允许无能力的只读执行。", command)
        if command.capabilities & {"service", "docker"} and profile != "operator":
            return PolicyResult(PolicyDecision.DENIED, "该主机控制能力需要主机操作档位。", command)
        return PolicyResult(PolicyDecision.APPROVAL_REQUIRED, "该操作需要浏览器授权。", command)

    def _parse_host_command(self, request: ToolRequest) -> HostCommand | None:
        try:
            value = json.loads(request.arguments_json)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            return None
        access = value.get("access", "read")
        raw_capabilities = value.get("capabilities", [])
        if access not in ("read", "write") or not isinstance(raw_capabilities, list):
            return None
        cwd = self._resolve_host_directory(
            value.get("cwd", str(self._workspace_root)), access
        )
        if cwd is None:
            return None
        if not all(
            isinstance(item, str) and item in self._host_capabilities for item in raw_capabilities
        ):
            return None
        capabilities = frozenset(raw_capabilities)
        if request.name == "terminal_exec":
            if set(value) - {"argv", "cwd", "access", "capabilities"} or "argv" not in value:
                return None
            raw_argv = value["argv"]
            if (
                not isinstance(raw_argv, list)
                or not 1 <= len(raw_argv) <= 32
                or not all(isinstance(item, str) and 0 < len(item) <= 256 for item in raw_argv)
            ):
                return None
            argv = tuple(self._normalize_model_home_placeholder(item) for item in raw_argv)
            if any("\x00" in item or any(char in item for char in "\n\r;&|<>`$") for item in argv):
                return None
            if "/" in argv[0] or argv[0].startswith("-"):
                return None
            return HostCommand("exec", argv, None, cwd, access, capabilities, self._home_root)
        if set(value) - {"script", "cwd", "access", "capabilities"} or "script" not in value:
            return None
        script = value["script"]
        if (
            not isinstance(script, str)
            or not script.strip()
            or len(script) > 8192
            or "\x00" in script
        ):
            return None
        return HostCommand("shell", (), script, cwd, access, capabilities, self._home_root)

    def _normalize_model_home_placeholder(self, value: str) -> str:
        """Replace the local model's `/home/user` placeholder with the real user home.

        Only lexical child paths are converted; traversal remains an untrusted command argument.
        """
        if value == "/home/user":
            return str(self._home_root)
        prefix = "/home/user/"
        if not value.startswith(prefix):
            return value
        relative = Path(value.removeprefix(prefix))
        if any(part in {"", ".", ".."} for part in relative.parts):
            return value
        return str(self._home_root / relative)

    def _resolve_host_directory(self, raw_path: object, access: TerminalAccess) -> Path | None:
        if (
            not isinstance(raw_path, str)
            or not raw_path
            or len(raw_path) > 4096
            or "\x00" in raw_path
        ):
            return None
        if raw_path == "/home/user":
            return self._workspace_root
        candidate = Path(raw_path)
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (self._workspace_root / candidate).resolve()
        )
        try:
            if not resolved.is_dir():
                return None
            if resolved == Path("/") and access == "read":
                return resolved
            if resolved.stat().st_uid != os.getuid():
                return None
        except OSError:
            return None
        if _is_sensitive_path(resolved, self._home_root):
            return None
        return resolved

def _is_sensitive_path(path: Path, home_root: Path) -> bool:
    """Keep credential-bearing home directories outside every terminal scope."""
    try:
        relative = path.relative_to(home_root)
    except ValueError:
        return False
    return any(
        part in {".ssh", ".aws", ".gnupg", ".config", ".docker", ".kube"} for part in relative.parts
    )
