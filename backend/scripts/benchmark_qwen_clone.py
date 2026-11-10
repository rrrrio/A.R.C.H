"""Measure a consented Qwen clone profile without persisting speech or transcript content."""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from arch.benchmarks.metrics import summary_dict
from arch.benchmarks.reporting import write_json_report
from arch.settings import get_settings
from arch.voice.profiles import VoiceProfile, VoiceProfileStore
from arch.voice.qwen_clone import QWEN_MODEL_REVISION, QwenCloneRuntime, split_clone_text

DEFAULT_TEXT = "你好，这是第一句。这里是第二句，用于测量本地声音克隆输出。"


def parse_args() -> argparse.Namespace:
    """Require an explicit consented profile rather than using any current selection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def require_profile(data_dir: Path, profile_id: str) -> VoiceProfile:
    """Resolve exactly one existing local profile without exposing its reference path."""
    profile = VoiceProfileStore(data_dir).get(profile_id)
    if profile is None:
        raise ValueError("--profile-id must identify an existing local voice profile.")
    return profile


async def measure(
    runtime: QwenCloneRuntime,
    profile: VoiceProfile,
    chunks: tuple[str, ...],
    runs: int,
) -> tuple[list[float], list[float], list[int]]:
    """Warm the worker once, then measure first and complete PCM availability per response."""
    if runs < 1:
        raise ValueError("--runs must be at least 1.")
    if not chunks:
        raise ValueError("--text must contain non-whitespace content.")
    await runtime.start()
    try:
        for chunk in chunks:
            await runtime.synthesize(chunk, profile)
        first_pcm_seconds: list[float] = []
        complete_pcm_seconds: list[float] = []
        pcm_bytes: list[int] = []
        for _ in range(runs):
            started = time.perf_counter()
            first_pcm: float | None = None
            total_bytes = 0
            for chunk in chunks:
                pcm, _ = await runtime.synthesize(chunk, profile)
                total_bytes += len(pcm)
                if first_pcm is None:
                    first_pcm = time.perf_counter() - started
            first_pcm_seconds.append(first_pcm if first_pcm is not None else 0.0)
            complete_pcm_seconds.append(time.perf_counter() - started)
            pcm_bytes.append(total_bytes)
        return first_pcm_seconds, complete_pcm_seconds, pcm_bytes
    finally:
        await runtime.stop()


def main() -> None:
    """Run the explicit local benchmark and write a timing-only JSON report."""
    args = parse_args()
    settings = get_settings()
    profile = require_profile(settings.data_dir, args.profile_id)
    chunks = split_clone_text(args.text)
    runtime = QwenCloneRuntime(settings.data_dir)
    first_pcm, complete_pcm, pcm_bytes = asyncio.run(measure(runtime, profile, chunks, args.runs))
    write_json_report(
        args.output,
        {
            "benchmark": "qwen-clone",
            "model_revision": QWEN_MODEL_REVISION,
            "runs": args.runs,
            "sentence_count": len(chunks),
            "text_characters": len(args.text),
            "first_sentence_pcm_seconds": summary_dict(first_pcm),
            "complete_response_pcm_seconds": summary_dict(complete_pcm),
            "pcm_bytes": summary_dict(pcm_bytes),
            "note": (
                "Warm local Unix-socket worker measurement. The report omits profile metadata, "
                "reference audio, transcript, and requested text. It excludes browser, network, "
                "speaker, and human-perceived latency."
            ),
        },
    )


if __name__ == "__main__":
    main()
