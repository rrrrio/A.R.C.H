"""Measure local Faster-Whisper transcription latency on a fixed WAV sample."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from arch.benchmarks.metrics import summary_dict
from arch.benchmarks.reporting import write_json_report
from arch.benchmarks.system import gpu_memory_used_mib


def parse_args() -> argparse.Namespace:
    """Parse explicit model, input, and output parameters."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--language", choices=("zh", "en"), default="zh")
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def transcribe_once(model: object, audio: Path, language: str) -> tuple[str, float]:
    """Force segment iteration so the measured operation includes decode and inference."""
    started = time.perf_counter()
    segments, _ = model.transcribe(str(audio), language=language, beam_size=1)  # type: ignore[attr-defined]
    transcript = "".join(segment.text for segment in segments).strip()
    return transcript, time.perf_counter() - started


def main() -> None:
    """Warm the requested model then record repeatable inference timings."""
    args = parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    if not args.audio.is_file():
        raise SystemExit(f"Audio file does not exist: {args.audio}")

    from faster_whisper import WhisperModel

    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    warmup_transcript, warmup_seconds = transcribe_once(model, args.audio, args.language)
    gpu_memory_mib = gpu_memory_used_mib()
    durations: list[float] = []
    transcript = warmup_transcript
    for _ in range(args.runs):
        transcript, duration = transcribe_once(model, args.audio, args.language)
        durations.append(duration)

    write_json_report(
        args.output,
        {
            "benchmark": "asr",
            "model": args.model,
            "device": args.device,
            "compute_type": args.compute_type,
            "language": args.language,
            "audio": str(args.audio),
            "warmup_seconds": warmup_seconds,
            "gpu_memory_used_mib_after_warmup": gpu_memory_mib,
            "transcript": transcript,
            "inference_seconds": summary_dict(durations),
        },
    )


if __name__ == "__main__":
    main()
