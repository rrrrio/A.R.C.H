"""Pipecat boundary for a selected Qwen voice-clone synthesis runtime."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path

from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService
from pipecat.transcriptions.language import Language
from pipecat.utils.tracing.service_decorators import traced_tts

from arch.voice.profiles import VoiceProfile

QWEN_OUTPUT_SAMPLE_RATE = 24000
QWEN_MODEL_REVISION = "c27fe8aa05b732b1376d0f6a1e522fbccb84abbd"
MAX_QWEN_MESSAGE_BYTES = 32 * 1024 * 1024
type CloneSynthesize = Callable[[str, VoiceProfile], Awaitable[tuple[bytes, int]]]
type CloneCancel = Callable[[], Awaitable[None]]


class QwenCloneUnavailable(RuntimeError):
    """Indicate that an explicitly selected clone mode cannot be started safely."""


class QwenCloneRuntime:
    """Own a selected-profile worker in the isolated Qwen Python environment."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._environment = data_dir / "runtime" / "qwen-tts"
        self._socket = self._environment / "worker.sock"
        self._process: asyncio.subprocess.Process | None = None

    def require_ready(self) -> None:
        """Fail before session startup when explicit model preparation has not completed."""
        provenance = (
            self._data_dir / "models" / "qwen3-tts" / QWEN_MODEL_REVISION / "arch-provenance.json"
        )
        if not provenance.is_file() or not (self._environment / "bin" / "python").is_file():
            raise QwenCloneUnavailable(
                "Run make qwen-tts-prepare APPLY=1 before selecting a Qwen clone profile."
            )

    async def start(self) -> None:
        """Start one local worker only after artifact and isolated-runtime checks succeed."""
        self.require_ready()
        if self._process is not None and self._process.returncode is None:
            return
        self._socket.unlink(missing_ok=True)
        worker = Path(__file__).parents[3] / "scripts" / "qwen_tts_worker.py"
        self._process = await asyncio.create_subprocess_exec(
            str(self._environment / "bin" / "python"),
            str(worker),
            "--data-dir",
            str(self._data_dir),
            "--socket",
            str(self._socket),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        for _ in range(100):
            if self._socket.is_socket():
                return
            if self._process.returncode is not None:
                break
            await asyncio.sleep(0.1)
        await self.stop()
        raise QwenCloneUnavailable("Qwen clone worker did not become ready.")

    async def synthesize(self, text: str, profile: VoiceProfile) -> tuple[bytes, int]:
        """Request one profile-bound PCM response without transmitting a local path."""
        if self._process is None or self._process.returncode is not None:
            raise QwenCloneUnavailable("Qwen clone worker is not running.")
        reader, writer = await asyncio.open_unix_connection(
            str(self._socket), limit=MAX_QWEN_MESSAGE_BYTES
        )
        try:
            await _write_message(writer, {"text": text, "profile_id": profile.id})
            response = await _read_message(reader)
        finally:
            writer.close()
            await writer.wait_closed()
        if not response.get("ok"):
            raise QwenCloneUnavailable("Qwen clone worker rejected synthesis.")
        audio = response.get("audio")
        sample_rate = response.get("sample_rate")
        if not isinstance(audio, str) or not isinstance(sample_rate, int):
            raise QwenCloneUnavailable("Qwen clone worker returned an invalid response.")
        try:
            return base64.b64decode(audio, validate=True), sample_rate
        except ValueError as error:
            raise QwenCloneUnavailable(
                "Qwen clone worker returned invalid audio encoding."
            ) from error

    async def cancel(self) -> None:
        """Terminate in-flight local generation; the next request starts a clean worker."""
        await self.stop()

    async def stop(self) -> None:
        """Stop the private worker and remove its local socket without touching profiles."""
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
        self._socket.unlink(missing_ok=True)


async def _write_message(writer: asyncio.StreamWriter, payload: dict[str, str]) -> None:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_QWEN_MESSAGE_BYTES:
        raise QwenCloneUnavailable("Qwen clone request is too large.")
    writer.write(struct.pack("!I", len(encoded)) + encoded)
    await writer.drain()


async def _read_message(reader: asyncio.StreamReader) -> dict[str, object]:
    header = await reader.readexactly(4)
    size = struct.unpack("!I", header)[0]
    if not 1 <= size <= MAX_QWEN_MESSAGE_BYTES:
        raise QwenCloneUnavailable("Qwen clone worker response is too large.")
    payload = json.loads((await reader.readexactly(size)).decode("utf-8"))
    if not isinstance(payload, dict):
        raise QwenCloneUnavailable("Qwen clone worker returned an invalid response.")
    return payload


class QwenCloneTTSService(TTSService):
    """Adapt a selected local Qwen profile runtime to Pipecat's normal TTS frames."""

    def __init__(
        self, profile: VoiceProfile, synthesize: CloneSynthesize, cancel: CloneCancel | None = None
    ) -> None:
        self._profile = profile
        self._synthesize = synthesize
        self._cancel_runtime = cancel
        super().__init__(
            push_start_frame=True,
            push_stop_frames=True,
            sample_rate=QWEN_OUTPUT_SAMPLE_RATE,
            settings=TTSSettings(
                model="Qwen3-TTS-12Hz-0.6B-Base", voice=profile.id, language=Language.ZH
            ),
        )

    def can_generate_metrics(self) -> bool:
        """Report that this adapter participates in Pipecat first-byte metrics."""
        return True

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        """Synthesize one profile-bound utterance and reject incompatible PCM formats."""
        try:
            await self.start_tts_usage_metrics(text)
            audio, sample_rate = await self._synthesize(text, self._profile)
            if sample_rate != QWEN_OUTPUT_SAMPLE_RATE:
                raise QwenCloneUnavailable(
                    "Qwen clone runtime returned "
                    f"{sample_rate} Hz instead of {QWEN_OUTPUT_SAMPLE_RATE} Hz."
                )
            if not audio or len(audio) % 2:
                raise QwenCloneUnavailable("Qwen clone runtime returned invalid PCM audio.")
            await self.stop_ttfb_metrics()
            yield TTSAudioRawFrame(
                audio=audio,
                sample_rate=QWEN_OUTPUT_SAMPLE_RATE,
                num_channels=1,
                context_id=context_id,
            )
        except QwenCloneUnavailable as error:
            yield ErrorFrame(error=f"Qwen clone mode unavailable: {error}")
        except asyncio.CancelledError:
            if self._cancel_runtime is not None:
                await self._cancel_runtime()
            raise
        except Exception:
            yield ErrorFrame(error="Qwen clone synthesis failed.")
        finally:
            await self.stop_ttfb_metrics()
