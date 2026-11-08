"""Tests for host-observation parsing without requiring an NVIDIA GPU."""

from local_voice_agent.benchmarks.system import parse_memory_mib


def test_parse_memory_mib_accepts_one_integer_value() -> None:
    """A normal nvidia-smi value is converted to an integer."""
    assert parse_memory_mib("2048\n") == 2048


def test_parse_memory_mib_rejects_non_numeric_output() -> None:
    """Unexpected output does not become a misleading benchmark value."""
    assert parse_memory_mib("N/A") is None
    assert parse_memory_mib("2048 MiB") is None
