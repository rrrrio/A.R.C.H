"""Regression coverage for timing-only target-device avatar acceptance reports."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    script_path = Path(__file__).parents[1] / "scripts" / "summarize_avatar_device_acceptance.py"
    specification = importlib.util.spec_from_file_location(
        "summarize_avatar_device_acceptance", script_path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _observation(index: int) -> dict[str, float | bool]:
    return {
        "audio_first_ms": float(index),
        "cancellation_ms": float(index * 10),
        "clarity_acceptable": True,
        "fps": 25.0,
        "video_first_ms": float(index + 4),
        "watermark_absent": True,
    }


def test_device_report_requires_exact_fixed_schema_and_summarizes_five_turns(
    tmp_path: Path,
) -> None:
    module = _load_script()
    input_path = tmp_path / "observations.json"
    input_path.write_text(json.dumps([_observation(index) for index in range(1, 6)]))

    observations = module.parse_observations(input_path, 5)
    report = module.summarize(observations)

    assert report["runs"] == 5
    assert report["decoded_start_skew_ms"] == {
        "count": 5,
        "maximum": 4.0,
        "p50": 4.0,
        "p95": 4.0,
        "unit": "ms",
    }
    assert report["delivered_fps"]["p50"] == 25.0
    assert report["cancellation_ms"]["p95"] == 50.0
    assert report["subjective"] == {
        "all_clarity_acceptable": True,
        "all_watermarks_absent": True,
    }


def test_device_report_rejects_incomplete_or_identifying_observations(tmp_path: Path) -> None:
    module = _load_script()
    input_path = tmp_path / "observations.json"
    input_path.write_text(json.dumps([_observation(1)] * 4))

    with pytest.raises(ValueError, match="exactly 5"):
        module.parse_observations(input_path, 5)

    with pytest.raises(ValueError, match="exactly 5"):
        module.parse_observations(input_path, 20)

    identifying = _observation(1) | {"transcript": "do not retain this"}
    input_path.write_text(json.dumps([identifying] * 5))
    with pytest.raises(ValueError, match="fixed timing schema"):
        module.parse_observations(input_path, 5)


def test_device_report_uses_median_p50_nearest_rank_p95_and_rejects_non_finite_values(
    tmp_path: Path,
) -> None:
    module = _load_script()
    input_path = tmp_path / "observations.json"
    observations = [_observation(index) for index in range(1, 6)]
    for index, observation in enumerate(observations, start=1):
        observation["fps"] = float(index)
    input_path.write_text(json.dumps(observations))

    report = module.summarize(module.parse_observations(input_path, 5))

    assert report["delivered_fps"]["p50"] == 3.0
    assert report["delivered_fps"]["p95"] == 5.0
    observations[0]["fps"] = float("nan")
    input_path.write_text(json.dumps(observations))
    with pytest.raises(ValueError, match="finite non-negative"):
        module.parse_observations(input_path, 5)
