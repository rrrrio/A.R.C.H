"""Measure a selected local TTS runner without assuming one vendor API."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
import time
from pathlib import Path

from local_voice_agent.benchmarks.metrics import summary_dict
from local_voice_agent.benchmarks.reporting import write_json_report


def parse_args() -> argparse.Namespace:
    """Parse a safe argv template after `--command`."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default="你好，这是本地语音基准测试。")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--command",
        nargs=argparse.REMAINDER,
        required=True,
        help="Trusted argv template containing {text} and {output_wav}; no shell is used.",
    )
    return parser.parse_args()


def main() -> None:
    """Execute the selected runner repeatedly and measure output availability."""
    args = parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    if not args.command:
        raise SystemExit("--command must contain an executable and arguments")

    durations: list[float] = []
    with tempfile.TemporaryDirectory(prefix="lva-tts-") as temporary_directory:
        temporary_path = Path(temporary_directory)
        for index in range(args.runs + 1):
            output_wav = temporary_path / f"run-{index}.wav"
            command = [
                argument.format(text=args.text, output_wav=output_wav)
                for argument in args.command
            ]
            started = time.perf_counter()
            subprocess.run(command, check=True, shell=False)
            elapsed = time.perf_counter() - started
            if not output_wav.is_file() or output_wav.stat().st_size == 0:
                raise RuntimeError(f"TTS runner did not create audio: {output_wav}")
            if index > 0:
                durations.append(elapsed)

    write_json_report(
        args.output,
        {
            "benchmark": "tts",
            "runner_argv_template": args.command,
            "text": args.text,
            "output_ready_seconds": summary_dict(durations),
            "note": (
                "This measures complete WAV availability. Streaming first-PCM timing requires "
                "a runner-specific adapter."
            ),
        },
    )


if __name__ == "__main__":
    main()
