"""Run the loopback-only avatar media control service."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from avatar_media_runtime.runtime import AvatarMediaRuntime
from avatar_media_runtime.server import create_runtime_app
from avatar_media_runtime.tickets import MediaTicketSigner
from avatar_media_runtime.wav2lip import LoadedWav2LipRenderer, Wav2LipBackend
from avatar_media_runtime.wav2lip_cache import Wav2LipCacheManifest, sha256_file


def main() -> None:
    """Start only the authenticated local control plane, never a LAN listener."""
    secret = os.environ.get("ARCH_AVATAR_MEDIA_TICKET_SECRET")
    if secret is None:
        raise SystemExit("ARCH_AVATAR_MEDIA_TICKET_SECRET is required for avatar media runtime.")
    try:
        signer = MediaTicketSigner(secret.encode("utf-8"))
    except ValueError as error:
        raise SystemExit(str(error)) from error
    app = create_runtime_app(AvatarMediaRuntime(_load_backend()), signer)
    uvicorn.run(app, host="127.0.0.1", port=8101, log_level="info")


def _load_backend() -> Wav2LipBackend | None:
    """Load Wav2Lip only when the operator explicitly enables this new runtime."""
    if os.environ.get("ARCH_AVATAR_MEDIA_BACKEND", "") != "wav2lip":
        return None
    root = Path.cwd()
    source = Path(
        os.environ.get("ARCH_AVATAR_WAV2LIP_SOURCE", root / "data/runtime/livetalking/source")
    )
    checkpoint = Path(
        os.environ.get("ARCH_AVATAR_WAV2LIP_MODEL", root / "data/runtime/livetalking/wav2lip.pth")
    )
    cache_root = Path(
        os.environ.get("ARCH_AVATAR_MEDIA_CACHE_DIR", root / "data/runtime/avatar-media/avatars")
    )
    source_video = Path(os.environ.get("ARCH_AVATAR_SOURCE_VIDEO", root / "data/avatars/idle.mp4"))
    manifest = Wav2LipCacheManifest(
        source_sha256=sha256_file(source_video),
        checkpoint_sha256=sha256_file(checkpoint),
        fps=25,
        crop_size=256,
        pads=(0, 10, 0, 0),
    )
    cache = cache_root / manifest.key
    manifest.write_or_validate(cache)
    return Wav2LipBackend(LoadedWav2LipRenderer(source, checkpoint, cache))


if __name__ == "__main__":
    main()
