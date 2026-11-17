"""Source-matched, watermark-free Wav2Lip cache preparation."""

from __future__ import annotations

import hashlib
import json
import pickle
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import av
import cv2
import numpy as np

GENERATOR_REVISION = "arch-avatar-media-wav2lip-v1-no-watermark"

FaceBox = tuple[int, int, int, int]
FaceDetector = Callable[[np.ndarray], FaceBox | None]


@dataclass(frozen=True, slots=True)
class Wav2LipCacheManifest:
    """All inputs whose change invalidates a prepared avatar cache."""

    source_sha256: str
    checkpoint_sha256: str
    fps: int
    crop_size: int
    pads: tuple[int, int, int, int]
    generator_revision: str = GENERATOR_REVISION

    @property
    def key(self) -> str:
        """Return a content address without encoding an operator path."""
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def write_or_validate(self, directory: Path) -> None:
        """Persist exactly one cache identity and reject mismatched reuse."""
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "manifest.json"
        contents = json.dumps(asdict(self), sort_keys=True, indent=2) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") != contents:
            raise RuntimeError("Wav2Lip cache manifest does not match the requested inputs.")
        path.write_text(contents, encoding="utf-8")


def sha256_file(path: Path) -> str:
    """Hash an operator-controlled media or checkpoint file by content."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_wav2lip_cache(
    source: Path,
    checkpoint: Path,
    destination_root: Path,
    detector: FaceDetector,
    *,
    fps: int = 25,
    crop_size: int = 256,
    pads: tuple[int, int, int, int] = (0, 10, 0, 0),
) -> Path:
    """Extract full frames and face crops without modifying source pixels."""
    if fps != 25:
        raise ValueError("Wav2Lip output cache must be prepared for 25 FPS.")
    if crop_size != 256:
        raise ValueError("Wav2Lip face crop size must be 256 pixels.")
    if not source.is_file() or not checkpoint.is_file():
        raise RuntimeError("Wav2Lip source video and checkpoint must both exist.")
    manifest = Wav2LipCacheManifest(
        source_sha256=sha256_file(source),
        checkpoint_sha256=sha256_file(checkpoint),
        fps=fps,
        crop_size=crop_size,
        pads=pads,
    )
    destination = destination_root / manifest.key
    if _is_complete(destination):
        manifest.write_or_validate(destination)
        return destination
    if destination.exists():
        raise RuntimeError("Wav2Lip cache is incomplete; refusing to overwrite it.")

    temporary = destination.with_name(f".{destination.name}.tmp")
    if temporary.exists():
        raise RuntimeError("Wav2Lip cache preparation is already in progress.")
    try:
        full_images = temporary / "full_imgs"
        face_images = temporary / "face_imgs"
        full_images.mkdir(parents=True)
        face_images.mkdir()
        coordinates: list[tuple[int, int, int, int]] = []
        with av.open(str(source)) as container:
            frames = list(container.decode(video=0))
        if not frames:
            raise RuntimeError("Wav2Lip source video contains no decodable frames.")
        for index, decoded in enumerate(frames):
            image = decoded.to_ndarray(format="bgr24")
            box = detector(image)
            if box is None:
                raise RuntimeError(f"Wav2Lip face detection failed at source frame {index}.")
            y1, y2, x1, x2 = _crop_bounds(box, image.shape, pads)
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                raise RuntimeError(f"Wav2Lip face crop is empty at source frame {index}.")
            if not cv2.imwrite(str(full_images / f"{index:08d}.png"), image):
                raise RuntimeError("Wav2Lip could not write a full source frame.")
            face = cv2.resize(crop, (crop_size, crop_size))
            if not cv2.imwrite(str(face_images / f"{index:08d}.png"), face):
                raise RuntimeError("Wav2Lip could not write a face crop.")
            coordinates.append((y1, y2, x1, x2))
        with (temporary / "coords.pkl").open("wb") as stream:
            pickle.dump(coordinates, stream)
        manifest.write_or_validate(temporary)
        destination_root.mkdir(parents=True, exist_ok=True)
        temporary.rename(destination)
        return destination
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _crop_bounds(
    box: FaceBox, shape: tuple[int, ...], pads: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    top, bottom, left, right = pads
    height, width = shape[:2]
    return (
        max(0, y1 - top),
        min(height, y2 + bottom),
        max(0, x1 - left),
        min(width, x2 + right),
    )


def _is_complete(directory: Path) -> bool:
    required = ("manifest.json", "coords.pkl", "full_imgs", "face_imgs")
    if not all((directory / name).exists() for name in required):
        return False
    full = sorted((directory / "full_imgs").glob("*.png"))
    face = sorted((directory / "face_imgs").glob("*.png"))
    if not full or [path.name for path in full] != [path.name for path in face]:
        return False
    try:
        with (directory / "coords.pkl").open("rb") as stream:
            coordinates = pickle.load(stream)
    except (OSError, pickle.UnpicklingError):
        return False
    return isinstance(coordinates, list) and len(coordinates) == len(full)
