"""Preview or install rendered A.R.C.H. user-level systemd service units."""

from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path

from arch.settings import PROJECT_ROOT

TEMPLATE_DIRECTORY = PROJECT_ROOT / "deploy" / "systemd"
UNIT_NAMES = (
    "arch-backend.service",
    "arch-avatar-media.service",
    "arch-gateway.service",
)
RESOURCE_DEFAULTS = {
    "ARCH_BACKEND_CPU_QUOTA": "800%",
    "ARCH_BACKEND_MEMORY_HIGH": "10G",
    "ARCH_BACKEND_MEMORY_MAX": "12G",
    "ARCH_BACKEND_TASKS_MAX": "512",
}
RESOURCE_PATTERNS = {
    "ARCH_BACKEND_CPU_QUOTA": re.compile(r"[1-9][0-9]*%"),
    "ARCH_BACKEND_MEMORY_HIGH": re.compile(r"[1-9][0-9]*[KMGT]"),
    "ARCH_BACKEND_MEMORY_MAX": re.compile(r"[1-9][0-9]*[KMGT]"),
    "ARCH_BACKEND_TASKS_MAX": re.compile(r"[1-9][0-9]*"),
}


def _quote(value: str) -> str:
    return f'"{value.replace("\\", "\\\\").replace("\"", "\\\"")}"'


def _escape_unit_path(value: str) -> str:
    return value.replace("%", "%%").replace("\\", "\\x5c").replace(" ", "\\x20")


def _resource_values(project_root: Path) -> dict[str, str]:
    """Read narrowly validated resource overrides without evaluating shell syntax."""
    file_values: dict[str, str] = {}
    environment_file = project_root / ".env"
    if environment_file.is_file():
        for line in environment_file.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name in RESOURCE_DEFAULTS:
                file_values[name] = value.strip()
    values = {
        name: os.environ.get(name, file_values.get(name, default))
        for name, default in RESOURCE_DEFAULTS.items()
    }
    for name, value in values.items():
        if RESOURCE_PATTERNS[name].fullmatch(value) is None:
            raise ValueError(f"{name} has an invalid resource value: {value!r}")
    if _memory_bytes(values["ARCH_BACKEND_MEMORY_HIGH"]) > _memory_bytes(
        values["ARCH_BACKEND_MEMORY_MAX"]
    ):
        raise ValueError("ARCH_BACKEND_MEMORY_HIGH must not exceed ARCH_BACKEND_MEMORY_MAX")
    return values


def _memory_bytes(value: str) -> int:
    """Convert the intentionally small accepted systemd memory syntax to bytes."""
    multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    return int(value[:-1]) * multipliers[value[-1]]


def render_units(project_root: Path, uv_binary: Path, docker_binary: Path) -> dict[str, str]:
    """Render unit templates with paths known to the local checkout."""
    execution_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    resource_values = _resource_values(project_root)
    replacements = {
        "@WORKING_DIRECTORY@": _escape_unit_path(str(project_root.resolve())),
        "@PROJECT_ROOT@": _quote(str(project_root.resolve())),
        "@UV_BIN@": _quote(str(uv_binary.resolve())),
        "@DOCKER_BIN@": _quote(str(docker_binary.resolve())),
        "@EXECUTION_PATH@": _quote(f"PATH={execution_path}"),
        "@ENVIRONMENT_FILE@": _escape_unit_path(str(project_root / ".env")),
        "@BACKEND_CPU_QUOTA@": resource_values["ARCH_BACKEND_CPU_QUOTA"],
        "@BACKEND_MEMORY_HIGH@": resource_values["ARCH_BACKEND_MEMORY_HIGH"],
        "@BACKEND_MEMORY_MAX@": resource_values["ARCH_BACKEND_MEMORY_MAX"],
        "@BACKEND_TASKS_MAX@": resource_values["ARCH_BACKEND_TASKS_MAX"],
        "@AVATAR_MEDIA_RUNTIME_SOURCE@": _quote(
            str(project_root / "avatar_media_runtime" / "src")
        ),
        "@MUSETALK_PYTHON@": _quote(
            str(project_root / "data" / "runtime" / "musetalk" / "bin" / "python")
        ),
    }
    rendered: dict[str, str] = {}
    for name in UNIT_NAMES:
        content = (TEMPLATE_DIRECTORY / f"{name}.in").read_text(encoding="utf-8")
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)
        rendered[name] = content
    return rendered


def install_units(
    unit_directory: Path, units: dict[str, str], *, replace: bool
) -> tuple[Path, ...]:
    """Write rendered units without replacing an existing user configuration by default."""
    unit_directory.mkdir(parents=True, exist_ok=True)
    destinations = tuple(unit_directory / name for name in units)
    existing = [destination for destination in destinations if destination.exists()]
    if existing and not replace:
        names = ", ".join(str(destination) for destination in existing)
        raise FileExistsError(f"User service units already exist: {names}")
    for destination in destinations:
        destination.write_text(units[destination.name], encoding="utf-8")
    return destinations


def main() -> None:
    """Print the user-service install plan unless the operator explicitly applies it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write rendered user service units.")
    parser.add_argument(
        "--replace", action="store_true", help="Replace existing unit files; requires --apply."
    )
    parser.add_argument(
        "--unit-directory",
        type=Path,
        default=Path.home() / ".config" / "systemd" / "user",
        help="Destination directory for user unit files.",
    )
    parser.add_argument(
        "--unit",
        action="append",
        choices=UNIT_NAMES,
        help="Render or install only this named unit; repeat to select multiple units.",
    )
    arguments = parser.parse_args()
    if arguments.replace and not arguments.apply:
        parser.error("--replace requires --apply")
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv must be available on PATH before installing user services.")
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("docker must be available on PATH before installing user services.")
    units = render_units(PROJECT_ROOT, Path(uv), Path(docker))
    selected_names = tuple(arguments.unit or UNIT_NAMES)
    units = {name: units[name] for name in selected_names}
    destinations = tuple(arguments.unit_directory / name for name in selected_names)
    if not arguments.apply:
        print("Dry run. No user service units were written.")
        for destination in destinations:
            print(f"Would write: {destination}")
        return
    try:
        for destination in install_units(
            arguments.unit_directory, units, replace=arguments.replace
        ):
            print(f"Wrote: {destination}")
    except FileExistsError as error:
        raise SystemExit(str(error)) from error
    print("Run systemctl --user daemon-reload, then enable the desired units explicitly.")


if __name__ == "__main__":
    main()
