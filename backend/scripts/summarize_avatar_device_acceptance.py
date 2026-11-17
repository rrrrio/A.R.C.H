"""Validate and summarize timing-only target-device avatar media observations."""

from __future__ import annotations

import argparse
import json
from math import isfinite
from pathlib import Path
from typing import TypedDict, cast

from arch.benchmarks.metrics import summary_dict


class DeviceObservation(TypedDict):
    """One target-device turn without identifiers, speech content, or media payloads."""

    audio_first_ms: float
    cancellation_ms: float
    clarity_acceptable: bool
    fps: float
    video_first_ms: float
    watermark_absent: bool


_REQUIRED_FIELDS = frozenset(DeviceObservation.__annotations__)


def parse_args() -> argparse.Namespace:
    """Require an explicit local observation file and report destination."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=20)
    return parser.parse_args()


def _number(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field} must be a finite non-negative number.")
    return float(value)


def parse_observations(path: Path, runs: int) -> list[DeviceObservation]:
    """Accept only the fixed, non-identifying schema required for device acceptance."""
    if runs < 20:
        raise ValueError("Device acceptance requires at least 20 turns.")
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or len(value) != runs:
        raise ValueError(f"Expected exactly {runs} target-device observations.")
    observations: list[DeviceObservation] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict) or set(item) != _REQUIRED_FIELDS:
            raise ValueError(f"Observation {index} must contain only the fixed timing schema.")
        raw = cast(dict[str, object], item)
        watermark_absent = raw["watermark_absent"]
        clarity_acceptable = raw["clarity_acceptable"]
        if not isinstance(watermark_absent, bool) or not isinstance(clarity_acceptable, bool):
            raise ValueError(f"Observation {index} subjective checks must be booleans.")
        observations.append(
            {
                "audio_first_ms": _number(raw["audio_first_ms"], "audio_first_ms"),
                "cancellation_ms": _number(raw["cancellation_ms"], "cancellation_ms"),
                "clarity_acceptable": clarity_acceptable,
                "fps": _number(raw["fps"], "fps"),
                "video_first_ms": _number(raw["video_first_ms"], "video_first_ms"),
                "watermark_absent": watermark_absent,
            }
        )
    return observations


def summarize(observations: list[DeviceObservation]) -> dict[str, object]:
    """Calculate median p50 and nearest-rank p95 without classifying device evidence."""
    skew_ms = [
        abs(observation["audio_first_ms"] - observation["video_first_ms"])
        for observation in observations
    ]
    fps = [observation["fps"] for observation in observations]
    cancellation_ms = [observation["cancellation_ms"] for observation in observations]
    return {
        "cancellation_ms": _metric_summary(cancellation_ms, "ms"),
        "decoded_start_skew_ms": _metric_summary(skew_ms, "ms"),
        "delivered_fps": _metric_summary(fps, "frames_per_second"),
        "runs": len(observations),
        "subjective": {
            "all_clarity_acceptable": all(item["clarity_acceptable"] for item in observations),
            "all_watermarks_absent": all(item["watermark_absent"] for item in observations),
        },
    }


def _metric_summary(samples: list[float], unit: str) -> dict[str, float | int | str]:
    """Name statistics by their actual unit instead of the generic helper's seconds label."""
    values = summary_dict(samples)
    return {
        "count": values["count"],
        "maximum": values["maximum_seconds"],
        "p50": values["p50_seconds"],
        "p95": values["p95_seconds"],
        "unit": unit,
    }


def main() -> None:
    """Write one ignored, timing-only target-device acceptance report."""
    args = parse_args()
    report = {
        "kind": "target-device-avatar-media-observation",
        "observations": parse_observations(args.input, args.runs),
    }
    report["summary"] = summarize(cast(list[DeviceObservation], report["observations"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"avatar-device-acceptance: wrote {args.output}")


if __name__ == "__main__":
    main()
