"""Tests for percentile calculations used by benchmark acceptance reports."""

import pytest

from local_voice_agent.benchmarks.metrics import summarize_seconds


def test_summary_uses_nearest_rank_for_p95() -> None:
    """A 20-run benchmark reports its 19th sorted value as p95."""
    summary = summarize_seconds(range(1, 21))

    assert summary.count == 20
    assert summary.p50_seconds == 10.5
    assert summary.p95_seconds == 19
    assert summary.maximum_seconds == 20


def test_summary_rejects_empty_and_negative_samples() -> None:
    """Invalid timing inputs cannot become apparently valid benchmark results."""
    with pytest.raises(ValueError, match="At least one"):
        summarize_seconds([])
    with pytest.raises(ValueError, match="cannot be negative"):
        summarize_seconds([-0.1])
