"""Regression tests for source-matched Wav2Lip cache preparation."""

from __future__ import annotations

from pathlib import Path

import av
import cv2
import numpy as np
import pytest

from avatar_media_runtime.wav2lip_cache import prepare_wav2lip_cache


def test_prepared_full_frames_preserve_source_top_left_without_watermark(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    checkpoint = tmp_path / "wav2lip.pth"
    checkpoint.write_bytes(b"checkpoint")
    _write_video(source)

    cache = prepare_wav2lip_cache(
        source, checkpoint, tmp_path / "avatars", lambda _frame: (2, 2, 12, 12)
    )
    full = cv2.imread(str(cache / "full_imgs" / "00000000.png"))
    face = cv2.imread(str(cache / "face_imgs" / "00000000.png"))

    assert full is not None
    assert tuple(full[0, 0]) == (0, 0, 0)
    assert face is not None and face.shape == (256, 256, 3)
    assert (cache / "manifest.json").is_file()


def test_cache_rejects_a_mismatched_checkpoint_without_reusing_frames(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    first_checkpoint = tmp_path / "first.pth"
    second_checkpoint = tmp_path / "second.pth"
    first_checkpoint.write_bytes(b"first")
    second_checkpoint.write_bytes(b"second")
    _write_video(source)
    root = tmp_path / "avatars"

    first = prepare_wav2lip_cache(source, first_checkpoint, root, lambda _frame: (2, 2, 12, 12))
    second = prepare_wav2lip_cache(source, second_checkpoint, root, lambda _frame: (2, 2, 12, 12))

    assert first != second
    assert (first / "manifest.json").read_text() != (second / "manifest.json").read_text()


def test_incomplete_content_address_is_never_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    checkpoint = tmp_path / "checkpoint.pth"
    checkpoint.write_bytes(b"checkpoint")
    _write_video(source)
    root = tmp_path / "avatars"
    expected = prepare_wav2lip_cache(source, checkpoint, root, lambda _frame: (2, 2, 12, 12))
    (expected / "face_imgs" / "00000000.png").unlink()

    with pytest.raises(RuntimeError, match="incomplete"):
        prepare_wav2lip_cache(source, checkpoint, root, lambda _frame: (2, 2, 12, 12))


def _write_video(destination: Path) -> None:
    with av.open(str(destination), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=25)
        stream.width = 16
        stream.height = 16
        stream.pix_fmt = "yuv420p"
        pixels = np.zeros((16, 16, 3), dtype=np.uint8)
        pixels[4:12, 4:12, 1] = 180
        for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format="bgr24")):
            container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
