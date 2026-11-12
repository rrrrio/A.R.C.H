"""Regression coverage for the dependency-isolated preview renderer."""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path

import av
import numpy as np
from avatar_runtime.audio import StreamingPcmResampler
from avatar_runtime.worker import AvatarWorkerConnection, MuseTalkRenderer, PreviewRenderer


class RecordingRenderer:
    """Record one IPC connection without depending on a GPU runtime."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes | int | str]] = []

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        self.calls.append(("begin", utterance_id, f"{sample_rate}:{emotion}"))

    async def push_audio(self, utterance_id: str, pcm: bytes, timestamp_ns: int) -> None:
        self.calls.append(("audio", utterance_id, pcm))

    async def cancel(self, utterance_id: str) -> None:
        self.calls.append(("cancel", utterance_id, ""))

    async def end(self, utterance_id: str) -> None:
        self.calls.append(("end", utterance_id, ""))

    async def frames(self):
        if False:
            yield b""


class RecordingWriter:
    """Minimal StreamWriter replacement for worker protocol tests."""

    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def write(self, data: bytes) -> None:
        self.messages.append(json.loads(data))

    async def drain(self) -> None:
        return

    def close(self) -> None:
        return

    async def wait_closed(self) -> None:
        return


class BlockingEngine:
    """Hold one completed GPU batch until the test applies cancellation."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def render(self, pcm: bytes) -> list[bytes]:
        self.started.set()
        self.release.wait(timeout=1)
        return [b"stale-frame"]


class PreviewRendererTests(unittest.TestCase):
    """Verify that the runtime can decode an MP4 and encode a browser frame alone."""

    def test_decodes_fixed_mp4_to_jpeg(self) -> None:
        """The isolated environment owns every dependency needed for preview frames."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "avatar.mp4"
            self._write_source(source)

            renderer = PreviewRenderer(source, fps=25)
            frame = asyncio.run(anext(renderer.frames()))

        self.assertTrue(frame.startswith(b"\xff\xd8"))

    @staticmethod
    def _write_source(destination: Path) -> None:
        """Create a minimal MP4 fixture without relying on repository media files."""
        with av.open(destination, mode="w") as container:
            stream = container.add_stream("mpeg4", rate=25)
            stream.width = 16
            stream.height = 16
            stream.pix_fmt = "yuv420p"
            source = np.zeros((16, 16, 3), dtype=np.uint8)
            source[:, :, 1] = 180
            frame = av.VideoFrame.from_ndarray(source, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)


class AvatarWorkerConnectionTests(unittest.TestCase):
    """Verify that control messages reach the renderer with cancellation fencing."""

    def test_forwards_only_active_utterance_pcm(self) -> None:
        """Cancellation must fence late PCM before the future GPU boundary."""
        renderer = RecordingRenderer()
        writer = RecordingWriter()
        source_pcm = b"\x01\x00\x02\x00\x03\x00"
        audio = base64.b64encode(source_pcm).decode("ascii")
        messages = [
            {"type": "begin", "utterance_id": "turn", "sample_rate": 24000, "emotion": "neutral"},
            {"type": "audio", "utterance_id": "turn", "timestamp_ns": 0, "pcm_b64": audio},
            {"type": "cancel", "utterance_id": "turn"},
            {"type": "audio", "utterance_id": "turn", "timestamp_ns": 1, "pcm_b64": audio},
            {"type": "end", "utterance_id": "turn"},
        ]

        async def exercise() -> None:
            reader = asyncio.StreamReader()
            for message in messages:
                reader.feed_data((json.dumps(message) + "\n").encode())
            reader.feed_eof()
            await AvatarWorkerConnection(writer, renderer).run(reader)  # type: ignore[arg-type]

        asyncio.run(exercise())

        self.assertEqual(
            renderer.calls,
            [
                ("begin", "turn", "16000:neutral"),
                ("audio", "turn", StreamingPcmResampler(24000, 16000).convert(source_pcm)),
                ("cancel", "turn", ""),
            ],
        )
        self.assertEqual(writer.messages, [{"type": "ready"}])


class MuseTalkRendererTests(unittest.TestCase):
    """Verify renderer-local cancellation after a synchronous GPU batch starts."""

    def test_cancel_discards_completed_stale_gpu_batch(self) -> None:
        """A renderer task that finishes after cancel must not publish its JPEG frames."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "avatar.mp4"
            source.write_bytes(b"fixed-source")
            engine = BlockingEngine()
            renderer = MuseTalkRenderer(
                source,
                fps=25,
                model_root=root / "models",
                cache_dir=root / "cache",
                source_root=root / "source",
                engine=engine,
            )

            async def exercise() -> None:
                await renderer.begin("old", 16000, "neutral")
                await renderer.push_audio("old", b"\x00\x00" * 1600, 0)
                await renderer.end("old")
                await asyncio.to_thread(engine.started.wait, 1)
                await renderer.cancel("old")
                engine.release.set()
                await asyncio.sleep(0.02)
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(anext(renderer.frames()), timeout=0.02)

            asyncio.run(exercise())
