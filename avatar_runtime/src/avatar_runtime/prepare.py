"""Prepare a content-addressed fixed-video MuseTalk cache in the isolated runtime."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from avatar_runtime.cache import AvatarCacheIdentity, sha256_file

REVISION = "0a89dec45a0192b824e3cf4daf96c239440c5ed8"


def cache_identity(source: Path, fps: int) -> AvatarCacheIdentity:
    """Return the exact preprocessing identity shared with the worker."""
    return AvatarCacheIdentity(sha256_file(source), REVISION, fps, 0, 10, "jaw")


def prepare(source: Path, fps: int, model_root: Path, cache_root: Path, source_root: Path) -> Path:
    """Run the official preparer once, then atomically publish its cache directory."""
    source = source.resolve()
    model_root = model_root.resolve()
    cache_root = cache_root.resolve()
    source_root = source_root.resolve()
    identity = cache_identity(source, fps)
    destination = cache_root / identity.key
    complete = (destination / "latents.pt").is_file() and (
        destination / "mask_coords.pkl"
    ).is_file()
    if complete:
        identity.write_manifest(destination)
        return destination
    if destination.exists():
        raise RuntimeError(f"Incomplete MuseTalk cache exists: {destination}")
    models_link = source_root / "models"
    if not models_link.exists():
        models_link.symlink_to(model_root.resolve(), target_is_directory=True)
    if models_link.resolve() != model_root.resolve():
        raise RuntimeError(f"MuseTalk source models path is not pinned: {models_link}")
    avatar_id = f"arch-{identity.key}"
    config = source_root / "configs" / "inference" / f"{avatar_id}.yaml"
    config.write_text(
        "\n".join(
            (
                f"{avatar_id}:",
                "  preparation: true",
                f"  video_path: {source}",
                "  bbox_shift: 0",
                "  audio_clips: {}",
                "",
            )
        ),
        encoding="utf-8",
    )
    try:
        subprocess.run(
            [
                sys.executable,
                "scripts/realtime_inference.py",
                "--version",
                "v15",
                "--fps",
                str(fps),
                "--unet_config",
                "models/musetalkV15/musetalk.json",
                "--unet_model_path",
                "models/musetalkV15/unet.pth",
                "--whisper_dir",
                "models/whisper",
                "--inference_config",
                str(config.relative_to(source_root)),
                "--skip_save_images",
            ],
            cwd=source_root,
            env={
                **os.environ,
                "PYTHONPATH": str(source_root)
                + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""),
            },
            check=True,
        )
        produced = source_root / "results" / "v15" / "avatars" / avatar_id
        if not (produced / "latents.pt").is_file():
            raise RuntimeError("MuseTalk preparation did not create latent cache.")
        cache_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced), str(destination))
        identity.write_manifest(destination)
        return destination
    finally:
        config.unlink(missing_ok=True)


def main() -> None:
    """Run cache preparation with explicit data-confined paths."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=25)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--musetalk-source", type=Path, required=True)
    arguments = parser.parse_args()
    print(
        prepare(
            arguments.source,
            arguments.fps,
            arguments.model_root,
            arguments.cache_dir,
            arguments.musetalk_source,
        )
    )


if __name__ == "__main__":
    main()
