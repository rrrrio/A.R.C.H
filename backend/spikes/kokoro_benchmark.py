"""Measure warm Chinese Kokoro synthesis with one resident model instance."""

from __future__ import annotations

import argparse
import io
import time
from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro
from misaki import zh

from local_voice_agent.benchmarks.metrics import summary_dict
from local_voice_agent.benchmarks.reporting import write_json_report


def main() -> None:
    """Warm the G2P/model pair and record WAV-ready latency for repeated text."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--voice-file", type=Path, required=True)
    parser.add_argument("--voice", default="zf_001")
    parser.add_argument("--text", default="你好，这是本地语音基准测试。")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    phonemes, _ = zh.ZHG2P(version="1.1")(args.text)
    engine = Kokoro(str(args.model), str(args.voice_file))
    engine.create(phonemes, voice=args.voice, speed=1.0, is_phonemes=True)
    synthesis_durations: list[float] = []
    wav_ready_durations: list[float] = []
    audio_durations: list[float] = []
    for _ in range(args.runs):
        started = time.perf_counter()
        samples, rate = engine.create(phonemes, voice=args.voice, speed=1.0, is_phonemes=True)
        synthesis_duration = time.perf_counter() - started
        wav_buffer = io.BytesIO()
        sf.write(wav_buffer, samples, rate, subtype="PCM_16", format="WAV")
        wav_ready_duration = time.perf_counter() - started
        synthesis_durations.append(synthesis_duration)
        wav_ready_durations.append(wav_ready_duration)
        audio_durations.append(len(samples) / rate)
    real_time_factors = [
        run / audio
        for run, audio in zip(synthesis_durations, audio_durations, strict=True)
    ]
    write_json_report(args.output, {
        "benchmark": "tts", "runtime": "kokoro-onnx", "voice": args.voice,
        "text": args.text, "synthesis_seconds": summary_dict(synthesis_durations),
        "wav_ready_seconds": summary_dict(wav_ready_durations),
        "audio_seconds": summary_dict(audio_durations),
        "real_time_factor": summary_dict(real_time_factors),
        "note": "Warm resident CPU ONNX baseline; WAV encoded in memory, disk writing excluded.",
    })


if __name__ == "__main__":
    main()
