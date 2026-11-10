"""Pinned local model paths required by the browser voice runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VoiceModelPaths:
    """Resolved local-only artifacts selected by the measured benchmark baseline."""

    asr_model: Path
    llm_model: Path
    llama_server: Path
    tts_model: Path
    tts_voices: Path

    @classmethod
    def from_data_dir(cls, data_dir: Path) -> VoiceModelPaths:
        """Resolve the exact revision-pinned artifacts below the configured data root."""
        return cls(
            asr_model=(
                data_dir
                / "models/faster-whisper/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
            ),
            llm_model=(
                data_dir
                / "models/qwen3/6a569868d07d3bd59e8b97fb001bf8c0b254bb20/Qwen3-8B-Q4_K_M.gguf"
            ),
            llama_server=data_dir / "runtime/llama-cpp/b12110/llama-server",
            tts_model=(
                data_dir
                / "models/kokoro-zh"
                / "a628ea5d6fbde96d1a85f691a6a00847829937f9e488021ba2c5359bc6ea08b5"
                / "onnx/model_fp16.onnx"
            ),
            tts_voices=(
                data_dir
                / "models/kokoro-zh/6cc0f0d2ebe369a68b0df87c2b65c1af8c0ac3e3/voices-v1.1-zh.bin"
            ),
        )

    def missing(self) -> tuple[Path, ...]:
        """Return every missing artifact, including the ASR directory and required files."""
        required_files = (self.llm_model, self.llama_server, self.tts_model, self.tts_voices)
        missing_files = tuple(path for path in required_files if not path.is_file())
        return (() if self.asr_model.is_dir() else (self.asr_model,)) + missing_files

    def require_ready(self) -> None:
        """Fail before session setup when any required local artifact is unavailable."""
        missing = self.missing()
        if missing:
            names = ", ".join(str(path) for path in missing)
            raise RuntimeError(f"Local voice models are not ready: {names}")
