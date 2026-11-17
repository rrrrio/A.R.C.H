"""Prepare a source-matched Wav2Lip cache without starting LiveTalking services."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from avatar_media_runtime.wav2lip_cache import FaceBox, FaceDetector, prepare_wav2lip_cache


def live_talking_face_detector(source_root: Path) -> FaceDetector:
    """Return only the pinned detector needed by the no-watermark cache builder."""
    source = source_root.resolve()
    detector_path = source / "avatars" / "wav2lip" / "face_detection"
    if not detector_path.is_dir():
        raise RuntimeError(
            f"Pinned LiveTalking Wav2Lip face detector is unavailable: {detector_path}"
        )
    sys.path.insert(0, str(source))
    try:
        import torch
        from avatars.wav2lip import face_detection
    except ImportError as error:
        raise RuntimeError("Wav2Lip face detector dependencies are unavailable.") from error
    if not torch.cuda.is_available():
        raise RuntimeError("Wav2Lip cache preparation requires CUDA; refusing CPU fallback.")
    detector = face_detection.FaceAlignment(
        face_detection.LandmarksType._2D, flip_input=False, device="cuda"
    )

    def detect(frame: np.ndarray) -> FaceBox | None:
        result = detector.get_detections_for_batch(np.asarray([frame]))[0]
        if result is None:
            return None
        x1, y1, x2, y2 = (int(value) for value in result)
        return x1, y1, x2, y2

    return detect


def main() -> None:
    """Prepare a cache with explicit, operator-controlled local input paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--cache-root", required=True, type=Path)
    parser.add_argument("--livetalking-source", required=True, type=Path)
    arguments = parser.parse_args()
    result = prepare_wav2lip_cache(
        arguments.source,
        arguments.checkpoint,
        arguments.cache_root,
        live_talking_face_detector(arguments.livetalking_source),
    )
    print(result)


if __name__ == "__main__":
    main()
