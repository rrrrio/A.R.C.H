"""Unix-socket worker for an isolated video-avatar renderer.

The preview renderer is intentionally dependency-light and is useful for testing
WebRTC, cancellation, and layout before GPU weights are installed. The MuseTalk
renderer is loaded through the same protocol by the separately prepared runtime.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import av

from avatar_runtime.audio import StreamingPcmResampler
from avatar_runtime.cache import AvatarCacheIdentity, sha256_file

logger = logging.getLogger("arch.avatar")


class Renderer(Protocol):
    """Renderer contract owned by one worker connection."""

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None: ...

    async def push_audio(self, utterance_id: str, pcm: bytes, timestamp_ns: int) -> None: ...

    async def cancel(self, utterance_id: str) -> None: ...

    async def end(self, utterance_id: str) -> None: ...

    async def close(self) -> None: ...

    def frames(self) -> AsyncIterator[bytes]: ...


class CompletedUtteranceEngine(Protocol):
    """Synchronous model boundary kept behind the async worker protocol."""

    def render(self, pcm: bytes) -> list[bytes]: ...


class PreviewRenderer:
    """Loop the fixed portrait source as a transport and layout smoke test."""

    def __init__(self, source: Path, fps: int) -> None:
        self._source = source
        self._fps = fps
        self._frames: list[bytes] = []
        self._index = 0
        self._load_source()

    def _load_source(self) -> None:
        with av.open(self._source) as container:
            for frame in container.decode(video=0):
                output = av.VideoFrame.from_ndarray(
                    frame.to_ndarray(format="bgr24"), format="bgr24"
                )
                packets = output.to_image()  # type: ignore[attr-defined]
                import io

                buffer = io.BytesIO()
                packets.save(buffer, format="JPEG", quality=82)
                self._frames.append(buffer.getvalue())
        if not self._frames:
            raise RuntimeError(f"Avatar source video has no frames: {self._source}")

    async def frames(self) -> AsyncIterator[bytes]:
        """Yield a bounded-rate preview frame sequence."""
        delay = 1 / self._fps
        while True:
            yield self._frames[self._index % len(self._frames)]
            self._index += 1
            await asyncio.sleep(delay)

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        """Keep preview independent of the speech stream."""

    async def push_audio(self, utterance_id: str, pcm: bytes, timestamp_ns: int) -> None:
        """Preview deliberately does not create a lip-sync signal."""

    async def cancel(self, utterance_id: str) -> None:
        """Preview has no utterance-specific output to discard."""

    async def end(self, utterance_id: str) -> None:
        """Preview keeps its idle loop alive after speech ends."""

    async def close(self) -> None:
        """Preview has no background GPU work to release."""


@dataclass(slots=True)
class _UtteranceBuffer:
    """PCM and publication state for the sole active MuseTalk utterance."""

    pcm: bytearray
    emitted_frames: int = 0
    rendering: bool = False
    ended: bool = False


class MuseTalkRenderer:
    """Run a pinned MuseTalk engine after a complete, cancellable utterance arrives."""

    _RIGHT_CONTEXT_FRAMES = 2
    _MIN_BATCH_FRAMES = 8

    def __init__(
        self,
        source: Path,
        fps: int,
        model_root: Path,
        cache_dir: Path,
        source_root: Path,
        engine: CompletedUtteranceEngine | None = None,
    ) -> None:
        self._fps = fps
        identity = AvatarCacheIdentity(
            source_sha256=sha256_file(source),
            musetalk_revision="0a89dec45a0192b824e3cf4daf96c239440c5ed8",
            fps=fps,
            bbox_shift=0,
            extra_margin=10,
            parsing_mode="jaw",
        )
        cache_dir = cache_dir / identity.key
        if cache_dir.exists():
            identity.write_manifest(cache_dir)
        if engine is None:
            # Keep heavyweight CUDA and upstream imports outside the normal preview path.
            from avatar_runtime.musetalk import MuseTalkEngine

            engine = MuseTalkEngine(source_root, model_root, cache_dir, fps)
        self._engine = engine
        self._buffers: dict[str, _UtteranceBuffer] = {}
        self._generation = 0
        self._frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=max(fps * 2, 8))
        self._tasks: set[asyncio.Task[None]] = set()

    async def frames(self) -> AsyncIterator[bytes]:
        """Yield only frames belonging to the latest uncancelled utterance."""
        while True:
            yield await self._frames.get()

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        """Start collecting renderer-normalized 16 kHz PCM for one utterance."""
        if sample_rate != 16000:
            raise ValueError("MuseTalk renderer requires 16 kHz PCM.")
        del emotion
        self._generation += 1
        self._buffers.clear()
        self._discard_frames()
        self._buffers[utterance_id] = _UtteranceBuffer(bytearray())

    async def push_audio(self, utterance_id: str, pcm: bytes, timestamp_ns: int) -> None:
        """Append bounded PCM; the worker already fenced inactive utterances."""
        del timestamp_ns
        buffer = self._buffers.get(utterance_id)
        if buffer is not None:
            buffer.pcm.extend(pcm)
            await self._schedule(utterance_id)

    async def cancel(self, utterance_id: str) -> None:
        """Fence a GPU batch even when its synchronous call cannot be interrupted."""
        self._generation += 1
        self._buffers.pop(utterance_id, None)
        self._discard_frames()

    async def end(self, utterance_id: str) -> None:
        """Render a complete utterance without writing video files or temporary WAVs."""
        buffer = self._buffers.get(utterance_id)
        if buffer is None or not buffer.pcm:
            return
        buffer.ended = True
        await self._schedule(utterance_id, flush=True)

    async def close(self) -> None:
        """Fence outstanding batches and wait for their tasks before socket teardown."""
        self._generation += 1
        self._buffers.clear()
        self._discard_frames()
        tasks = tuple(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _schedule(self, utterance_id: str, *, flush: bool = False) -> None:
        buffer = self._buffers.get(utterance_id)
        if buffer is None or buffer.rendering:
            return
        total_frames = self._frame_count(buffer.pcm)
        ready_frames = total_frames if flush else max(0, total_frames - self._RIGHT_CONTEXT_FRAMES)
        if ready_frames <= buffer.emitted_frames:
            return
        if not flush and ready_frames - buffer.emitted_frames < self._MIN_BATCH_FRAMES:
            return
        buffer.rendering = True
        generation = self._generation
        task = asyncio.create_task(
            self._render_snapshot(utterance_id, bytes(buffer.pcm), ready_frames, generation)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _render_snapshot(
        self, utterance_id: str, pcm: bytes, ready_frames: int, generation: int
    ) -> None:
        frames = await asyncio.to_thread(self._engine.render, pcm)
        buffer = self._buffers.get(utterance_id)
        if generation != self._generation or buffer is None:
            return
        first_frame = buffer.emitted_frames
        available_frames = min(ready_frames, len(frames))
        if available_frames <= first_frame:
            buffer.rendering = False
            logger.error(
                "MuseTalk inference returned no new frames for utterance %s.", utterance_id
            )
            return
        for frame in frames[first_frame:available_frames]:
            if self._frames.full():
                self._frames.get_nowait()
            self._frames.put_nowait(frame)
        buffer.emitted_frames = available_frames
        buffer.rendering = False
        if buffer.ended and buffer.emitted_frames >= self._frame_count(buffer.pcm):
            self._buffers.pop(utterance_id, None)
            return
        await self._schedule(utterance_id, flush=buffer.ended)

    def _frame_count(self, pcm: bytearray) -> int:
        return len(pcm) // 2 * self._fps // 16000

    def _discard_frames(self) -> None:
        while not self._frames.empty():
            self._frames.get_nowait()


class AvatarWorkerConnection:
    """Serve one A.R.C.H. bridge connection and stream renderer frames."""

    def __init__(self, writer: asyncio.StreamWriter, renderer: Renderer) -> None:
        self._writer = writer
        self._renderer = renderer
        self._write_lock = asyncio.Lock()
        self._render_task: asyncio.Task[None] | None = None
        self._closed = False
        self._active_utterance_id: str | None = None
        self._resampler: StreamingPcmResampler | None = None

    async def run(self, reader: asyncio.StreamReader) -> None:
        await self._send({"type": "ready"})
        try:
            async for line in self._lines(reader):
                message = json.loads(line)
                kind = message.get("type")
                if kind == "hello":
                    self._start_rendering()
                elif kind == "begin":
                    await self._begin(message)
                elif kind == "audio":
                    await self._push_audio(message)
                elif kind == "cancel":
                    await self._cancel(message)
                elif kind == "end":
                    await self._end(message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._send({"type": "error", "detail": str(error)})
        finally:
            await self.close()

    async def _lines(self, reader: asyncio.StreamReader) -> AsyncIterator[bytes]:
        while True:
            line = await reader.readline()
            if not line:
                return
            yield line

    def _start_rendering(self) -> None:
        if self._render_task is not None:
            return
        self._render_task = asyncio.create_task(self._render())

    async def _begin(self, message: dict[str, object]) -> None:
        utterance_id = self._required_str(message, "utterance_id")
        sample_rate = self._required_positive_int(message, "sample_rate")
        emotion = self._required_str(message, "emotion")
        if self._active_utterance_id is not None:
            await self._renderer.cancel(self._active_utterance_id)
        self._resampler = StreamingPcmResampler(sample_rate, 16000)
        await self._renderer.begin(utterance_id, 16000, emotion)
        self._active_utterance_id = utterance_id
        self._start_rendering()

    async def _push_audio(self, message: dict[str, object]) -> None:
        utterance_id = self._required_str(message, "utterance_id")
        if utterance_id != self._active_utterance_id:
            return
        encoded = self._required_str(message, "pcm_b64")
        timestamp_ns = self._required_positive_int(message, "timestamp_ns", allow_zero=True)
        try:
            pcm = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise ValueError("audio pcm_b64 is not valid base64") from error
        assert self._resampler is not None
        converted = self._resampler.convert(pcm)
        if converted:
            await self._renderer.push_audio(utterance_id, converted, timestamp_ns)

    async def _cancel(self, message: dict[str, object]) -> None:
        utterance_id = self._required_str(message, "utterance_id")
        if utterance_id != self._active_utterance_id:
            return
        await self._renderer.cancel(utterance_id)
        self._active_utterance_id = None
        self._resampler = None

    async def _end(self, message: dict[str, object]) -> None:
        utterance_id = self._required_str(message, "utterance_id")
        if utterance_id != self._active_utterance_id:
            return
        await self._renderer.end(utterance_id)
        self._active_utterance_id = None
        self._resampler = None

    @staticmethod
    def _required_str(message: dict[str, object], field: str) -> str:
        value = message.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty string")
        return value

    @staticmethod
    def _required_positive_int(
        message: dict[str, object], field: str, *, allow_zero: bool = False
    ) -> int:
        value = message.get(field)
        lower_bound = 0 if allow_zero else 1
        if not isinstance(value, int) or isinstance(value, bool) or value < lower_bound:
            raise ValueError(f"{field} must be an integer of at least {lower_bound}")
        return value

    async def _render(self) -> None:
        try:
            frames = self._renderer.frames()
            async for payload in frames:
                await self._send(
                    {
                        "type": "frame",
                        "jpeg_b64": base64.b64encode(payload).decode("ascii"),
                    }
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("Avatar renderer stopped.")
            await self._send({"type": "error", "detail": str(error)})

    async def _send(self, message: dict[str, object]) -> None:
        if self._closed:
            return
        async with self._write_lock:
            self._writer.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
            await self._writer.drain()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._render_task is not None:
            self._render_task.cancel()
            await asyncio.gather(self._render_task, return_exceptions=True)
        await self._renderer.close()
        self._writer.close()
        await self._writer.wait_closed()


async def serve(
    socket_path: Path,
    source: Path,
    fps: int,
    backend: str,
    model_root: Path | None = None,
    cache_dir: Path | None = None,
    source_root: Path | None = None,
) -> None:
    """Bind the private IPC socket and serve one single-user renderer session."""
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()
    if backend == "preview":
        renderer: Renderer = PreviewRenderer(source, fps)
    else:
        if model_root is None or cache_dir is None or source_root is None:
            raise RuntimeError(
                "MuseTalk backend requires --model-root, --cache-dir, and --musetalk-source."
            )
        renderer = MuseTalkRenderer(source, fps, model_root, cache_dir, source_root)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = AvatarWorkerConnection(writer, renderer)
        await connection.run(reader)

    server = await asyncio.start_unix_server(handle, path=socket_path)
    os.chmod(socket_path, 0o600)
    logger.info("avatar worker ready: backend=%s socket=%s", backend, socket_path)
    async with server:
        await server.serve_forever()


def main() -> None:
    """Run the isolated avatar worker from systemd or a local smoke test."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=25)
    parser.add_argument("--backend", choices=("musetalk", "preview"), default="musetalk")
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--musetalk-source", type=Path)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(
        serve(
            arguments.socket,
            arguments.source,
            arguments.fps,
            arguments.backend,
            arguments.model_root,
            arguments.cache_dir,
            arguments.musetalk_source,
        )
    )


if __name__ == "__main__":
    main()
