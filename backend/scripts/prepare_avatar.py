"""Validate the operator-provided avatar source without downloading model weights."""

from __future__ import annotations

import argparse
from pathlib import Path

import av

from arch.settings import get_settings


def inspect_source(source: Path) -> dict[str, int | str]:
    """Read bounded video metadata required by the avatar worker."""
    if not source.is_file():
        raise FileNotFoundError(f"Avatar source video not found: {source}")
    with av.open(source) as container:
        stream = next((item for item in container.streams if item.type == "video"), None)
        if stream is None:
            raise ValueError("Avatar source video has no video stream.")
        frames = sum(1 for _ in container.decode(video=stream.index))
        if frames == 0:
            raise ValueError("Avatar source video has no decodable frames.")
        return {
            "path": str(source),
            "width": int(getattr(stream, "width", 0) or 0),
            "height": int(getattr(stream, "height", 0) or 0),
            "frames": frames,
            "fps": str(
                getattr(stream, "average_rate", None)
                or getattr(stream, "base_rate", None)
                or "unknown"
            ),
        }


def main() -> None:
    """Validate the configured local source and print its media facts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=None)
    arguments = parser.parse_args()
    settings = get_settings()
    source = (
        settings.avatar_source_video
        if arguments.source is None
        else arguments.source.resolve()
    )
    print(inspect_source(source))
    print(f"model={settings.avatar_model} worker_backend={settings.avatar_worker_backend}")


if __name__ == "__main__":
    main()
