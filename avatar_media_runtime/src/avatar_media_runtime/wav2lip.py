"""Whole-utterance Wav2Lip backend for the isolated media runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import av
import numpy as np

from avatar_media_runtime.contracts import PreparedMedia


class Wav2LipRenderer(Protocol):
    """GPU-only renderer boundary with no WebRTC or browser knowledge."""

    def render(self, pcm_16khz: bytes, *, frame_offset: int) -> Sequence[np.ndarray]: ...


class Wav2LipBackend:
    """Preserve 24 kHz playback while rendering a 16 kHz copy at 25 FPS."""

    def __init__(self, renderer: Wav2LipRenderer) -> None:
        self._renderer = renderer
        self._frame_offset = 0
        self._cancelled: set[str] = set()

    async def prepare(self, pcm_24khz: bytes, utterance_id: str) -> PreparedMedia:
        """Render a complete answer before publishing either synchronized track."""
        if len(pcm_24khz) % 2:
            raise ValueError("Wav2Lip PCM must contain whole 16-bit samples.")
        pcm_16khz = resample_pcm_24khz_to_16khz(pcm_24khz)
        frames = await asyncio.to_thread(
            self._renderer.render, pcm_16khz, frame_offset=self._frame_offset
        )
        if utterance_id in self._cancelled:
            self._cancelled.remove(utterance_id)
            raise RuntimeError("Wav2Lip preparation was cancelled.")
        video = [av.VideoFrame.from_ndarray(frame, format="bgr24") for frame in frames]
        self._frame_offset += len(video)
        return PreparedMedia(utterance_id, pcm_24khz, video, fps=25)

    async def cancel(self, utterance_id: str) -> None:
        """Fence a completed GPU result before it reaches the negotiated tracks."""
        self._cancelled.add(utterance_id)


def resample_pcm_24khz_to_16khz(pcm_24khz: bytes) -> bytes:
    """Linearly resample signed mono PCM without changing the playback source."""
    if len(pcm_24khz) % 2:
        raise ValueError("PCM must contain whole 16-bit samples.")
    source = np.frombuffer(pcm_24khz, dtype=np.int16)
    if source.size == 0:
        return b""
    target_size = source.size * 2 // 3
    source_positions = np.arange(source.size, dtype=np.float64)
    target_positions = np.arange(target_size, dtype=np.float64) * 1.5
    resampled = np.interp(target_positions, source_positions, source).round().astype(np.int16)
    return resampled.tobytes()


class LoadedWav2LipRenderer:
    """Lazy CUDA renderer copied from the pinned source without its HTTP/TTS layers."""

    def __init__(self, source_root: Path, checkpoint: Path, cache: Path, *, fps: int = 25) -> None:
        self._source_root = source_root.resolve()
        self._checkpoint = checkpoint.resolve()
        self._cache = cache.resolve()
        self._fps = fps
        self._validate()
        self._load()

    def _validate(self) -> None:
        required = (
            self._source_root / "avatars/wav2lip/audio.py",
            self._source_root / "avatars/wav2lip/models/wav2lip.py",
            self._checkpoint,
            self._cache / "manifest.json",
            self._cache / "coords.pkl",
            self._cache / "full_imgs",
            self._cache / "face_imgs",
        )
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError("Wav2Lip runtime is incomplete: " + ", ".join(missing))
        if self._fps != 25:
            raise ValueError("Wav2Lip renderer requires 25 FPS.")

    def _load(self) -> None:
        import pickle
        import sys

        import cv2
        import torch

        sys.path.insert(0, str(self._source_root))
        from avatars.wav2lip.audio import melspectrogram
        from avatars.wav2lip.models import Wav2Lip

        if not torch.cuda.is_available():
            raise RuntimeError("Wav2Lip renderer requires CUDA; refusing CPU fallback.")
        self._cv2 = cv2
        self._torch = torch
        self._melspectrogram = melspectrogram
        self._model = Wav2Lip()
        state = torch.load(self._checkpoint, map_location="cpu").get("state_dict")
        if not isinstance(state, dict):
            raise TypeError("Wav2Lip checkpoint state_dict must be a dictionary.")
        self._model.load_state_dict(
            {key.removeprefix("module."): value for key, value in state.items()}
        )
        self._model = self._model.eval().cuda()
        with (self._cache / "coords.pkl").open("rb") as stream:
            self._coordinates = pickle.load(stream)
        self._full = self._load_images(self._cache / "full_imgs")
        self._faces = self._load_images(self._cache / "face_imgs")
        if len(self._full) != len(self._faces) or len(self._full) != len(self._coordinates):
            raise RuntimeError("Wav2Lip cache is incomplete or inconsistent.")

    def _load_images(self, directory: Path) -> list[np.ndarray]:
        images = [self._cv2.imread(str(path)) for path in sorted(directory.glob("*.png"))]
        if not images or any(image is None for image in images):
            raise RuntimeError("Wav2Lip cache image could not be decoded.")
        return images

    @staticmethod
    def mirror_index(size: int, index: int) -> int:
        """Follow source frames forward then reverse at each loop boundary."""
        if size <= 0:
            raise ValueError("Wav2Lip cache must contain frames.")
        turn, offset = divmod(index, size)
        return offset if turn % 2 == 0 else size - offset - 1

    def render(self, pcm_16khz: bytes, *, frame_offset: int) -> Sequence[np.ndarray]:
        """Compose BGR source-sized frames without a JPEG encode/decode step."""
        samples = np.frombuffer(pcm_16khz, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return []
        mel = self._melspectrogram(samples)
        frame_count = max(1, samples.size * self._fps // 16_000)
        chunks = []
        for index in range(frame_count):
            start = max(0, min(int(index * 80 / self._fps), mel.shape[1] - 16))
            chunks.append(mel[:, start : start + 16])
        rendered: list[np.ndarray] = []
        for offset in range(0, len(chunks), 8):
            count = min(8, len(chunks) - offset)
            faces = [
                self._faces[self.mirror_index(len(self._faces), frame_offset + offset + item)]
                for item in range(count)
            ]
            batch = np.asarray(faces)
            masked = batch.copy()
            masked[:, batch.shape[1] // 2 :] = 0
            image = np.concatenate((masked, batch), axis=3).astype(np.float32) / 255.0
            mel_batch = np.asarray(chunks[offset : offset + count], dtype=np.float32).reshape(
                count, 80, 16, 1
            )
            image_tensor = self._torch.from_numpy(image.transpose(0, 3, 1, 2)).cuda()
            mel_tensor = self._torch.from_numpy(mel_batch.transpose(0, 3, 1, 2)).cuda()
            with self._torch.no_grad():
                predicted = (
                    self._model(mel_tensor, image_tensor).cpu().numpy().transpose(0, 2, 3, 1)
                    * 255.0
                )
            for item, face in enumerate(predicted):
                index = self.mirror_index(len(self._full), frame_offset + offset + item)
                y1, y2, x1, x2 = self._coordinates[index]
                frame = self._full[index].copy()
                frame[y1:y2, x1:x2] = self._cv2.resize(face.astype(np.uint8), (x2 - x1, y2 - y1))
                rendered.append(frame)
        return rendered
