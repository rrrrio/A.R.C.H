"""Pinned MuseTalk 1.5 adapter used only by the Python 3.10 GPU runtime.

The upstream project exposes a file-to-file demo.  This module keeps its model
objects resident, uses the same VAE/UNet/Whisper operations, and returns JPEG
frames to the worker without creating output videos or exposing an HTTP port.
"""

from __future__ import annotations

import os
import pickle
import sys
from collections.abc import Callable
from pathlib import Path


class MuseTalkEngine:
    """Load one pinned upstream checkout and render completed 16 kHz utterances."""

    def __init__(self, source_root: Path, model_root: Path, cache_dir: Path, fps: int) -> None:
        self._source_root = source_root.resolve()
        self._model_root = model_root.resolve()
        self._cache_dir = cache_dir.resolve()
        self._fps = fps
        self._validate_layout()
        self._load()

    def _validate_layout(self) -> None:
        required = (
            self._source_root / "musetalk" / "utils" / "audio_processor.py",
            self._model_root / "musetalkV15" / "unet.pth",
            self._model_root / "musetalkV15" / "musetalk.json",
            self._model_root / "sd-vae" / "diffusion_pytorch_model.bin",
            self._model_root / "whisper" / "pytorch_model.bin",
            self._model_root / "dwpose" / "dw-ll_ucoco_384.pth",
            self._model_root / "face-parse-bisent" / "79999_iter.pth",
            self._model_root / "face-parse-bisent" / "resnet18-5c106cde.pth",
            self._cache_dir / "latents.pt",
            self._cache_dir / "coords.pkl",
            self._cache_dir / "mask_coords.pkl",
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise RuntimeError("MuseTalk runtime is incomplete: " + ", ".join(missing))

    def _load(self) -> None:
        # Upstream modules resolve DWPose and face-parser paths relative to cwd.
        # The worker has no untrusted paths, so a private checkout-local models link is safe.
        models_link = self._source_root / "models"
        if not models_link.exists():
            models_link.symlink_to(self._model_root, target_is_directory=True)
        if models_link.resolve() != self._model_root:
            raise RuntimeError(f"MuseTalk source models path is not pinned: {models_link}")
        os.chdir(self._source_root)
        sys.path.insert(0, str(self._source_root))

        import cv2  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
        import torch  # type: ignore[import-not-found]
        from musetalk.utils.audio_processor import AudioProcessor  # type: ignore[import-not-found]
        from musetalk.utils.blending import get_image_blending  # type: ignore[import-not-found]
        from musetalk.utils.utils import load_all_model  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
        from transformers import WhisperModel  # type: ignore[import-not-found]

        self._cv2 = cv2
        self._np = np
        self._torch = torch
        self._image = Image
        self._blend: Callable[..., object] = get_image_blending
        self._device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        if self._device.type != "cuda":
            raise RuntimeError("MuseTalk renderer requires CUDA; refusing CPU fallback.")
        self._vae, self._unet, self._pe = load_all_model(
            unet_model_path=str(self._model_root / "musetalkV15" / "unet.pth"),
            unet_config=str(self._model_root / "musetalkV15" / "musetalk.json"),
            vae_type="sd-vae",
            device=self._device,
        )
        self._pe = self._pe.half().to(self._device)
        self._vae.vae = self._vae.vae.half().to(self._device)
        self._unet.model = self._unet.model.half().to(self._device)
        self._weight_dtype = self._unet.model.dtype
        self._whisper = WhisperModel.from_pretrained(str(self._model_root / "whisper"))
        self._whisper = self._whisper.to(device=self._device, dtype=self._weight_dtype).eval()
        self._whisper.requires_grad_(False)
        self._audio = AudioProcessor(feature_extractor_path=str(self._model_root / "whisper"))
        self._timesteps = torch.tensor([0], device=self._device)
        self._latents = torch.load(self._cache_dir / "latents.pt", map_location="cpu")
        with (self._cache_dir / "coords.pkl").open("rb") as stream:
            self._coords = pickle.load(stream)
        with (self._cache_dir / "mask_coords.pkl").open("rb") as stream:
            self._mask_coords = pickle.load(stream)
        self._frames = self._load_images(self._cache_dir / "full_imgs")
        self._masks = self._load_images(self._cache_dir / "mask")
        if not self._frames or not self._masks or len(self._frames) != len(self._latents):
            raise RuntimeError("MuseTalk fixed-video cache is incomplete or inconsistent.")

    def _load_images(self, directory: Path) -> list[object]:
        images = sorted(directory.glob("*.png"), key=lambda path: int(path.stem))
        return [self._cv2.imread(str(path)) for path in images]

    @staticmethod
    def _cycle_index(frame_offset: int, batch_offset: int, index: int, cycle_length: int) -> int:
        """Map one local diffusion item to the matching fixed-video cycle item."""
        return (frame_offset + batch_offset + index) % cycle_length

    def render(self, pcm: bytes, *, frame_offset: int = 0) -> list[bytes]:
        """Run the official path for PCM whose first frame is ``frame_offset``."""
        if len(pcm) % 2:
            raise ValueError("MuseTalk PCM must contain 16-bit samples.")
        samples = self._np.frombuffer(pcm, dtype=self._np.int16).astype(self._np.float32) / 32768.0
        if not len(samples):
            return []
        features = self._audio.feature_extractor(
            samples, return_tensors="pt", sampling_rate=16000
        ).input_features
        features = features.to(device=self._device, dtype=self._weight_dtype)
        hidden = self._whisper.encoder(features, output_hidden_states=True).hidden_states
        whisper = self._torch.stack(hidden, dim=2)
        frame_count = int((len(samples) / 16000) * self._fps)
        if frame_count == 0:
            return []
        prompt_length = 10
        padding = self._torch.zeros_like(whisper[:, :4])
        whisper = self._torch.cat(
            (padding, whisper, self._torch.zeros_like(whisper[:, :12])), dim=1
        )
        prompts = []
        multiplier = 50 / self._fps
        for index in range(frame_count):
            start = int(index * multiplier)
            prompt = whisper[:, start : start + prompt_length]
            if prompt.shape[1] != prompt_length:
                break
            prompts.append(prompt)
        if not prompts:
            return []
        prompts_tensor = self._torch.cat(prompts, dim=0).reshape(
            len(prompts), prompt_length * 5, 384
        )
        rendered: list[bytes] = []
        batch_size = 8
        for offset in range(0, len(prompts_tensor), batch_size):
            prompt_batch = prompts_tensor[offset : offset + batch_size]
            latent_batch = self._torch.cat(
                [
                    self._latents[
                        self._cycle_index(frame_offset, offset, index, len(self._latents))
                    ]
                    for index in range(len(prompt_batch))
                ],
                dim=0,
            ).to(device=self._device, dtype=self._weight_dtype)
            with self._torch.no_grad():
                audio_features = self._pe(prompt_batch.to(self._device))
                predicted = self._unet.model(
                    latent_batch, self._timesteps, encoder_hidden_states=audio_features
                ).sample
                decoded = self._vae.decode_latents(predicted.to(dtype=self._vae.vae.dtype))
            for index, face in enumerate(decoded):
                frame_index = self._cycle_index(frame_offset, offset, index, len(self._frames))
                x1, y1, x2, y2 = self._coords[frame_index]
                face = self._cv2.resize(face.astype(self._np.uint8), (x2 - x1, y2 - y1))
                combined = self._blend(
                    self._frames[frame_index].copy(),
                    face,
                    self._coords[frame_index],
                    self._masks[frame_index],
                    self._mask_coords[frame_index],
                )
                ok, encoded = self._cv2.imencode(
                    ".jpg", combined, [self._cv2.IMWRITE_JPEG_QUALITY, 95]
                )
                if not ok:
                    raise RuntimeError("MuseTalk could not encode a rendered JPEG frame.")
                rendered.append(encoded.tobytes())
        return rendered
