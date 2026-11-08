"""Synthesize one Chinese sentence with the pinned local Kokoro model."""

from __future__ import annotations

import argparse
from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro
from misaki import zh


def parse_args() -> argparse.Namespace:
    """Read only explicit local paths and synthesis settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--voice-file", type=Path, required=True)
    parser.add_argument("--voice", default="zf_001")
    parser.add_argument("--text", required=True)
    parser.add_argument("--output-wav", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    """Convert Chinese text to phonemes and write 24 kHz PCM WAV output."""
    args = parse_args()
    if not args.model.is_file() or not args.voice_file.is_file():
        raise SystemExit("Kokoro model and voice file must exist.")
    phonemes, _ = zh.ZHG2P(version="1.1")(args.text)
    samples, sample_rate = Kokoro(str(args.model), str(args.voice_file)).create(
        phonemes,
        voice=args.voice,
        speed=1.0,
        is_phonemes=True,
    )
    args.output_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.output_wav, samples, sample_rate, subtype="PCM_16")


if __name__ == "__main__":
    main()
