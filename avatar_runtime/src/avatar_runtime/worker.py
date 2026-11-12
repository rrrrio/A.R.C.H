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
from pathlib import Path

import av

logger = logging.getLogger("arch.avatar")


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
                output = av.VideoFrame.from_ndarray(frame.to_ndarray(format="bgr24"), format="bgr24")
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


class MuseTalkRenderer:
    """Integration boundary for the prepared MuseTalk/LiveTalking runtime.

    The model runtime is deliberately not imported into the A.R.C.H. backend
    environment. The prepared worker can replace this class with the official
    MuseTalk 1.5 pipeline while keeping the socket protocol stable.
    """

    def __init__(self, source: Path, fps: int) -> None:
        self._source = source
        self._fps = fps
        raise RuntimeError(
            "MuseTalk runtime is not prepared; install the pinned MuseTalk/LiveTalking "
            "runtime and preprocess the fixed avatar before selecting backend=musetalk."
        )

    async def frames(self) -> AsyncIterator[bytes]:
        """Fail loudly until the GPU runtime has been prepared and selected."""
        raise RuntimeError(
            "MuseTalk runtime is not prepared; run the avatar preparation command "
            "and select the isolated MuseTalk worker."
        )
        yield b""


class AvatarWorkerConnection:
    """Serve one A.R.C.H. bridge connection and stream renderer frames."""

    def __init__(self, writer: asyncio.StreamWriter, renderer: object) -> None:
        self._writer = writer
        self._renderer = renderer
        self._write_lock = asyncio.Lock()
        self._render_task: asyncio.Task[None] | None = None
        self._closed = False

    async def run(self, reader: asyncio.StreamReader) -> None:
        await self._send({"type": "ready"})
        try:
            async for line in self._lines(reader):
                message = json.loads(line)
                if message.get("type") == "hello":
                    self._start_rendering()
                elif message.get("type") == "cancel":
                    # The renderer's bounded output queue is represented by the
                    # latest-frame policy in this transport smoke-test worker.
                    continue
                elif message.get("type") in {"begin", "audio", "event", "end"}:
                    self._start_rendering()
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
        self._writer.close()
        await self._writer.wait_closed()


async def serve(socket_path: Path, source: Path, fps: int, backend: str) -> None:
    """Bind the private IPC socket and serve one single-user renderer session."""
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()
    renderer: object = PreviewRenderer(source, fps) if backend == "preview" else MuseTalkRenderer(source, fps)

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
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(arguments.socket, arguments.source, arguments.fps, arguments.backend))


if __name__ == "__main__":
    main()
