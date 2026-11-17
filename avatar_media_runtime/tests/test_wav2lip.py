"""Wav2Lip media backend regressions independent of CUDA model loading."""

from __future__ import annotations

import asyncio

import numpy as np

from avatar_media_runtime.wav2lip import (
    LoadedWav2LipRenderer,
    Wav2LipBackend,
    resample_pcm_24khz_to_16khz,
)


class Renderer:
    def __init__(self) -> None:
        self.pcm = b""
        self.offset = -1

    def render(self, pcm_16khz: bytes, *, frame_offset: int) -> list[np.ndarray]:
        self.pcm = pcm_16khz
        self.offset = frame_offset
        return [np.zeros((8, 8, 3), dtype=np.uint8) for _ in range(2)]

    def idle_frame(self, *, frame_offset: int) -> np.ndarray:
        return np.full((8, 8, 3), frame_offset + 1, dtype=np.uint8)


def test_backend_preserves_24khz_playback_and_uses_a_16khz_copy() -> None:
    renderer = Renderer()
    backend = Wav2LipBackend(renderer)
    pcm = (np.arange(2_400, dtype=np.int16)).tobytes()

    prepared = asyncio.run(backend.prepare(pcm, "utterance"))

    assert prepared.audio_pcm_24khz == pcm
    assert len(renderer.pcm) == len(pcm) * 2 // 3
    assert prepared.fps == 25
    assert len(prepared.video_frames) == 2


def test_backend_provides_source_idle_frames_in_sequence() -> None:
    backend = Wav2LipBackend(Renderer())

    first = backend.next_idle_frame()
    second = backend.next_idle_frame()

    assert int(first.to_ndarray(format="bgr24")[0, 0, 0]) == 1
    assert int(second.to_ndarray(format="bgr24")[0, 0, 0]) == 2


def test_mirror_index_reverses_at_the_cache_boundary() -> None:
    indexes = [LoadedWav2LipRenderer.mirror_index(3, index) for index in range(8)]
    assert indexes == [0, 1, 2, 2, 1, 0, 0, 1]


def test_resampler_rejects_partial_pcm_samples() -> None:
    try:
        resample_pcm_24khz_to_16khz(b"x")
    except ValueError as error:
        assert "whole" in str(error)
    else:
        raise AssertionError("partial PCM must be rejected")
