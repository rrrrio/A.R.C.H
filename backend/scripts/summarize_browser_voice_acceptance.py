"""Repeat isolated browser voice checks and summarize server-stage observations."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

from arch.benchmarks.metrics import summary_dict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNNER = PROJECT_ROOT / "backend" / "scripts" / "run_browser_voice_e2e.py"
RESULT_PREFIX = "browser-e2e: result "


class LatencyObservation(TypedDict):
    """One server stage observed in the browser UI without transcript content."""

    stage: str
    elapsed_ms: int


class BrowserObservation(TypedDict):
    """The structured result emitted by one isolated browser invocation."""

    barge_in: bool
    latency: list[LatencyObservation]


def parse_args() -> argparse.Namespace:
    """Accept an explicit run count and output location for a repeatable report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--barge-in", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def parse_driver_result(output: str) -> BrowserObservation:
    """Extract the final typed observation while preserving ordinary driver logs."""
    payloads = [
        line.removeprefix(RESULT_PREFIX)
        for line in output.splitlines()
        if line.startswith(RESULT_PREFIX)
    ]
    if len(payloads) != 1:
        raise ValueError("Browser driver did not emit exactly one structured result.")
    value: object = json.loads(payloads[0])
    if not isinstance(value, dict) or not isinstance(value.get("barge_in"), bool):
        raise ValueError("Browser driver emitted an invalid result header.")
    raw_latency = value.get("latency")
    if not isinstance(raw_latency, list):
        raise ValueError("Browser driver emitted invalid latency observations.")
    latency: list[LatencyObservation] = []
    for sample in raw_latency:
        if not isinstance(sample, dict) or not isinstance(sample.get("stage"), str):
            raise ValueError("Browser driver emitted an invalid latency stage.")
        elapsed_ms = sample.get("elapsed_ms")
        if not isinstance(elapsed_ms, int) or elapsed_ms < 0:
            raise ValueError("Browser driver emitted an invalid latency duration.")
        latency.append({"stage": sample["stage"], "elapsed_ms": elapsed_ms})
    return {"barge_in": value["barge_in"], "latency": latency}


def summarize(
    observations: list[BrowserObservation], required_stage: str
) -> dict[str, float | int]:
    """Summarize one required server stage after every run observed it exactly once."""
    samples: list[float] = []
    for observation in observations:
        values = [
            sample["elapsed_ms"]
            for sample in observation["latency"]
            if sample["stage"] == required_stage
        ]
        if len(values) != 1:
            raise ValueError(f"Expected exactly one {required_stage} observation per run.")
        samples.append(values[0] / 1_000)
    return summary_dict(samples)


def run_once(args: argparse.Namespace) -> BrowserObservation:
    """Run the isolated browser helper once and return its structured observation."""
    command = [sys.executable, str(RUNNER), "--timeout-seconds", str(args.timeout_seconds)]
    if args.barge_in:
        command.append("--assert-barge-in")
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, text=True, capture_output=True, check=False
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode:
        raise RuntimeError(f"Browser acceptance run failed with exit code {completed.returncode}.")
    return parse_driver_result(completed.stdout)


def main() -> None:
    """Generate one JSON report from repeated isolated browser observations."""
    args = parse_args()
    if args.runs < 20:
        raise SystemExit("--runs must be at least 20 for acceptance reporting")
    observations = [run_once(args) for _ in range(args.runs)]
    required_stage = "playback-stopped" if args.barge_in else "playback-first-audio"
    report = {
        "kind": "synthetic-browser-server-stage",
        "runs": args.runs,
        "barge_in": args.barge_in,
        "required_stage": required_stage,
        "summary_seconds": summarize(observations, required_stage),
        "observations": observations,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"browser-acceptance: wrote {args.output}")


if __name__ == "__main__":
    main()
