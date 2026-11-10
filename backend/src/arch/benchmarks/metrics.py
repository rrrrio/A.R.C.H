"""Small, dependency-free helpers for benchmark result summaries."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from math import ceil
from statistics import median


@dataclass(frozen=True)
class Summary:
    """Distribution values expressed in seconds for one measured operation."""

    count: int
    maximum_seconds: float
    p50_seconds: float
    p95_seconds: float


def summarize_seconds(samples: Iterable[float]) -> Summary:
    """Return nearest-rank p50/p95 and maximum for non-negative durations."""
    values = sorted(samples)
    if not values:
        raise ValueError("At least one sample is required.")
    if any(value < 0 for value in values):
        raise ValueError("Durations cannot be negative.")

    def nearest_rank(percentile: float) -> float:
        index = max(0, ceil(percentile * len(values)) - 1)
        return values[index]

    return Summary(
        count=len(values),
        maximum_seconds=values[-1],
        p50_seconds=median(values),
        p95_seconds=nearest_rank(0.95),
    )


def summary_dict(samples: Iterable[float]) -> dict[str, float | int]:
    """Convert a duration summary to JSON-compatible data."""
    return asdict(summarize_seconds(samples))
