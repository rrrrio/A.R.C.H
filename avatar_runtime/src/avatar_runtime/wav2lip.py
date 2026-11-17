"""Private Wav2Lip renderer backed by a pinned LiveTalking checkout.

The adapter consumes complete 16 kHz PCM utterances.  It intentionally does
not import LiveTalking's HTTP, WebRTC, TTS, or session-management layers.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path


class Wav2LipEngine:
    """Render a complete utterance using the supplied Wav2Lip avatar cache."""

    def __init__(
        self, source_root: Path, model_path: Path, avatar_path: Path, fps: int
    ) -> None:
        self._source_root = source_root.resolve()
        self._model_path = model_path.resolve()
        self._avatar_path = avatar_path.resolve()
        self._fps = fps
        self._validate()
        self._load()

    def _validate(self) -> None:
        required = (
            self._source_root / "avatars" / "wav2lip" / "audio.py",
            self._source_root / "avatars" / "wav2lip" / "models" / "wav2lip.py",
            self._model_path,
            self._avatar_path / "coords.pkl",
            self._avatar_path / "full_imgs",
            self._avatar_path / "face_imgs",
        )
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError("Wav2Lip runtime is incomplete: " + ", ".join(missing))

    def _load(self) -> None:
        sys.path.insert(0, str(self._source_root))
        import cv2  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
        import torch  # type: ignore[import-not-found]
        from avatars.wav2lip.audio import melspectrogram  # type: ignore[import-not-found]
        from avatars.wav2lip.models import Wav2Lip  # type: ignore[import-not-found]

        if not torch.cuda.is_available():
            raise RuntimeError("Wav2Lip renderer requires CUDA; refusing CPU fallback.")
        self._cv2 = cv2
        self._np = np
        self._torch = torch
        self._model = Wav2Lip()
        checkpoint = torch.load(self._model_path, map_location="cpu")
        state = checkpoint.get("state_dict")
        if not isinstance(state, dict):
            raise TypeError("Wav2Lip checkpoint state_dict must be a dictionary.")
        self._model.load_state_dict(
            {key.removeprefix("module."): value for key, value in state.items()}
        )
        self._model = self._model.eval().cuda()
        self._melspectrogram = melspectrogram
        with (self._avatar_path / "coords.pkl").open("rb") as stream:
            self._coords = pickle.load(stream)
        self._frames = self._load_images(self._avatar_path / "full_imgs")
        self._faces = self._load_images(self._avatar_path / "face_imgs")
        if not self._frames or len(self._frames) != len(self._faces) or len(self._frames) != len(self._coords):
            raise RuntimeError("Wav2Lip avatar cache is incomplete or inconsistent.")

    def _load_images(self, directory: Path) -> list[object]:
        paths = sorted(directory.glob("*.png"), key=lambda path: int(path.stem))
        images = [self._cv2.imread(str(path)) for path in paths]
        if any(image is None for image in images):
            raise RuntimeError(f"Wav2Lip avatar image could not be decoded: {directory}")
        return images

    @staticmethod
    def _mirror_index(size: int, index: int) -> int:
        """Match LiveTalking's forward-then-reverse avatar frame traversal."""
        if size <= 0:
            raise ValueError("Wav2Lip avatar frame count must be positive.")
        turn, offset = divmod(index, size)
        return offset if turn % 2 == 0 else size - offset - 1

    def render(self, pcm: bytes, *, frame_offset: int = 0) -> list[bytes]:
        """Produce JPEG portrait frames for a complete 16 kHz PCM utterance."""
        if len(pcm) % 2:
            raise ValueError("Wav2Lip PCM must contain 16-bit samples.")
        if not pcm:
            return []
        samples = self._np.frombuffer(pcm, dtype=self._np.int16).astype(self._np.float32) / 32768.0
        mel = self._melspectrogram(samples)
        multiplier = 80.0 / self._fps
        frame_count = max(1, len(samples) * self._fps // 16000)
        chunks = []
        for index in range(frame_count):
            start = int(index * multiplier)
            if start + 16 > mel.shape[1]:
                start = max(0, mel.shape[1] - 16)
            chunks.append(mel[:, start : start + 16])
        rendered: list[bytes] = []
        for offset in range(0, len(chunks), 8):
            count = min(8, len(chunks) - offset)
            faces = [
                self._faces[self._mirror_index(len(self._faces), frame_offset + offset + item)]
                for item in range(count)
            ]
            image_batch = self._np.asarray(faces)
            masked = image_batch.copy()
            masked[:, image_batch.shape[1] // 2 :] = 0
            image_batch = self._np.concatenate((masked, image_batch), axis=3).astype(self._np.float32) / 255.0
            mel_batch = self._np.asarray(chunks[offset : offset + count], dtype=self._np.float32).reshape(
                count, 80, 16, 1
            )
            image_tensor = self._torch.from_numpy(image_batch.transpose(0, 3, 1, 2)).cuda()
            mel_tensor = self._torch.from_numpy(mel_batch.transpose(0, 3, 1, 2)).cuda()
            with self._torch.no_grad():
                predicted = self._model(mel_tensor, image_tensor).cpu().numpy().transpose(0, 2, 3, 1) * 255.0
            for item, face in enumerate(predicted):
                frame_index = self._mirror_index(len(self._frames), frame_offset + offset + item)
                y1, y2, x1, x2 = self._coords[frame_index]
                combined = self._frames[frame_index].copy()
                combined[y1:y2, x1:x2] = self._cv2.resize(face.astype(self._np.uint8), (x2 - x1, y2 - y1))
                ok, encoded = self._cv2.imencode(".jpg", combined, [self._cv2.IMWRITE_JPEG_QUALITY, 95])
                if not ok:
                    raise RuntimeError("Wav2Lip could not encode a rendered JPEG frame.")
                rendered.append(encoded.tobytes())
        return rendered
