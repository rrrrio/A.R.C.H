"""Bounded host observations used in benchmark reports."""

from __future__ import annotations

import subprocess


def parse_memory_mib(output: str) -> int | None:
    """Parse a single `nvidia-smi` memory value without accepting ambiguous output."""
    value = output.strip()
    return int(value) if value.isdecimal() else None


def gpu_memory_used_mib() -> int | None:
    """Return current GPU memory for the first device, or None if unavailable."""
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        return None
    return parse_memory_mib(result.stdout.splitlines()[0]) if result.stdout.splitlines() else None
