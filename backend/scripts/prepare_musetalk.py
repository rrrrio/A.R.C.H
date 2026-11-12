"""Describe the pinned MuseTalk preparation boundary without downloading by default."""

from __future__ import annotations

import argparse
from pathlib import Path

from arch.settings import Settings, get_settings

MUSETALK_REPOSITORY = "https://github.com/TMElyralab/MuseTalk.git"
MUSETALK_REVISION = "0a89dec45a0192b824e3cf4daf96c239440c5ed8"
MODEL_REPOSITORY = "TMElyralab/MuseTalk"
MODEL_REVISION = "2bcb936e2fddb4d86db4c62fd45b387d0c061571"
V15_UNET_SHA256 = "7ebf6c98c181e20838e4c0054e96e944ac60d5d692cc01db42839fe11b787007"
V15_UNET_BYTES = 3400074924


def model_root(settings: Settings) -> Path:
    """Return the data-confined destination reserved for one pinned renderer source."""
    return settings.data_dir / "models" / "musetalk" / MUSETALK_REVISION


def main() -> None:
    """Print the exact preparation plan; reject writes until artifact hashes are pinned."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Request artifact preparation.")
    arguments = parser.parse_args()
    settings = get_settings()
    destination = model_root(settings)
    if arguments.apply:
        raise RuntimeError(
            "MuseTalk apply is disabled until every required upstream weight has a reviewed "
            "source, license, and SHA-256 manifest. No files were written."
        )
    print("Dry run. No MuseTalk source, weights, cache, or runtime was created.")
    print(f"Source: {MUSETALK_REPOSITORY}@{MUSETALK_REVISION}")
    print(f"V1.5 UNet: {MODEL_REPOSITORY}@{MODEL_REVISION} musetalkV15/unet.pth")
    print(f"V1.5 UNet SHA-256: {V15_UNET_SHA256} ({V15_UNET_BYTES} bytes)")
    print(f"Destination: {destination}")
    print("Prerequisites: >=100 GiB free disk and a renderer maintenance window with GPU headroom.")
    print("Apply remains disabled until the reviewed artifact manifest is committed.")


if __name__ == "__main__":
    main()
