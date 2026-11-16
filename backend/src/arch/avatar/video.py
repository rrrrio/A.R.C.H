"""Authenticated video-avatar signaling and the local renderer bridge."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol, cast

import av
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError, VideoStreamTrack
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

# Keep Base64 JSON audio records well below asyncio's default 64 KiB line limit.
_MAX_IPC_PCM_CHUNK_BYTES = 24_000
_IPC_STREAM_LIMIT = 256 * 1024


class AvatarWorker(Protocol):
    """Minimal control surface between A.R.C.H. and the GPU worker."""

    async def start(self, on_frame: Callable[[bytes], Awaitable[None]]) -> None: ...

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None: ...

    async def push_audio(
        self, utterance_id: str, audio: bytes, timestamp_ns: int
    ) -> None: ...

    async def push_event(self, utterance_id: str, event: str) -> None: ...

    async def cancel(self, utterance_id: str) -> None: ...

    async def end(self, utterance_id: str) -> None: ...

    async def close(self) -> None: ...


class AvatarVideoTrack(VideoStreamTrack):
    """Turn JPEG frames from the local worker into an aiortc video track."""

    kind = "video"

    def __init__(
        self,
        fps: int,
        queue_size: int,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__()
        self._fps = fps
        self._frames: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=queue_size)
        self._frame_index = 0
        self._clock = clock
        self._sleep = sleep
        self._next_frame_at: float | None = None

    async def recv(self) -> av.VideoFrame:
        """Wait for the next worker frame and assign a stable presentation timestamp."""
        payload = await self._frames.get()
        if payload is None:
            raise MediaStreamError
        await self._wait_for_next_frame()
        with av.open(io.BytesIO(payload), format="mjpeg") as container:
            input_container: Any = container
            frame = next(input_container.decode(video=0))
        frame.pts = self._frame_index
        frame.time_base = Fraction(1, self._fps)
        self._frame_index += 1
        return cast(av.VideoFrame, frame)

    async def publish(self, payload: bytes) -> None:
        """Publish one frame and let WebRTC pacing backpressure the renderer."""
        if self.readyState == "ended":
            return
        await self._frames.put(payload)

    async def stop_stream(self) -> None:
        """End the track and wake a pending receiver."""
        if self.readyState == "ended":
            return
        await self.publish_end()
        super().stop()

    async def publish_end(self) -> None:
        """Wake the WebRTC sender when the owning avatar session closes."""
        while self._frames.full():
            self._frames.get_nowait()
        self._frames.put_nowait(None)

    async def _wait_for_next_frame(self) -> None:
        """Emit a GPU-rendered burst at the negotiated frame rate."""
        now = self._clock() if self._clock is not None else asyncio.get_running_loop().time()
        if self._next_frame_at is None:
            self._next_frame_at = now
            return
        self._next_frame_at += 1 / self._fps
        delay = self._next_frame_at - now
        if delay > 0:
            if self._sleep is not None:
                await self._sleep(delay)
            else:
                await asyncio.sleep(delay)
        elif -delay > 1 / self._fps:
            # Do not replay an old burst at maximum speed after a scheduling stall.
            self._next_frame_at = now


class UnixSocketAvatarWorker:
    """Line-delimited JSON IPC client for the isolated GPU avatar process."""

    def __init__(self, socket_path: Path, startup_timeout_ms: int = 15000) -> None:
        self._socket_path = socket_path
        self._startup_timeout = startup_timeout_ms / 1000
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._on_frame: Callable[[bytes], Awaitable[None]] | None = None
        self._ready = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self._closing = False
        self._sample_rates: dict[str, int] = {}

    async def start(self, on_frame: Callable[[bytes], Awaitable[None]]) -> None:
        """Connect to the already supervised worker and wait for its readiness event."""
        self._closing = False
        self._on_frame = on_frame
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(
                self._socket_path, limit=_IPC_STREAM_LIMIT
            )
        except OSError as error:
            raise RuntimeError("Avatar worker is not reachable.") from error
        self._reader_task = asyncio.create_task(self._read_messages())
        await self._send({"type": "hello", "model": "musetalk-1.5"})
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self._startup_timeout)
        except TimeoutError as error:
            raise RuntimeError("Avatar worker did not become ready.") from error

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        if sample_rate <= 0:
            raise ValueError("Avatar PCM sample rate must be positive.")
        self._sample_rates[utterance_id] = sample_rate
        await self._send(
            {
                "type": "begin",
                "utterance_id": utterance_id,
                "sample_rate": sample_rate,
                "emotion": emotion,
            }
        )

    async def push_audio(self, utterance_id: str, audio: bytes, timestamp_ns: int) -> None:
        sample_rate = self._sample_rates.get(utterance_id)
        if sample_rate is None:
            raise RuntimeError("Avatar PCM arrived before its utterance started.")
        if len(audio) % 2:
            raise ValueError("Avatar PCM must contain whole 16-bit samples.")
        for offset in range(0, len(audio), _MAX_IPC_PCM_CHUNK_BYTES):
            chunk = audio[offset : offset + _MAX_IPC_PCM_CHUNK_BYTES]
            chunk_timestamp_ns = timestamp_ns + (offset // 2) * 1_000_000_000 // sample_rate
            await self._send(
                {
                    "type": "audio",
                    "utterance_id": utterance_id,
                    "timestamp_ns": chunk_timestamp_ns,
                    "pcm_b64": base64.b64encode(chunk).decode("ascii"),
                }
            )

    async def push_event(self, utterance_id: str, event: str) -> None:
        await self._send({"type": "event", "utterance_id": utterance_id, "event": event})

    async def cancel(self, utterance_id: str) -> None:
        try:
            await self._send({"type": "cancel", "utterance_id": utterance_id})
        finally:
            self._sample_rates.pop(utterance_id, None)

    async def end(self, utterance_id: str) -> None:
        try:
            await self._send({"type": "end", "utterance_id": utterance_id})
        finally:
            self._sample_rates.pop(utterance_id, None)

    async def close(self) -> None:
        """Close the IPC stream and cancel its reader without killing systemd."""
        self._closing = True
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                # The worker may have already closed after reporting an IPC error.
                pass
        if self._reader_task is not None and self._reader_task is not asyncio.current_task():
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        self._reader = None
        self._writer = None
        self._reader_task = None
        self._ready.clear()
        self._sample_rates.clear()

    async def _send(self, message: dict[str, object]) -> None:
        if self._writer is None:
            raise RuntimeError("Avatar worker is not connected.")
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        async with self._write_lock:
            self._writer.write(data)
            await self._writer.drain()

    async def _read_messages(self) -> None:
        assert self._reader is not None
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    if self._closing:
                        return
                    raise RuntimeError("Avatar worker disconnected.")
                message = json.loads(line)
                kind = message.get("type")
                if kind == "ready":
                    self._ready.set()
                elif kind == "frame" and self._on_frame is not None:
                    encoded = message.get("jpeg_b64")
                    if isinstance(encoded, str):
                        await self._on_frame(base64.b64decode(encoded))
                elif kind == "error":
                    logger.error("Avatar worker error: %s", message.get("detail", "unknown"))
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._closing:
                logger.exception("Avatar worker IPC reader stopped.")
            self._ready.clear()


@dataclass(frozen=True, slots=True)
class AvatarVideoStatus:
    """Public status returned to the authenticated browser."""

    enabled: bool
    state: str
    model: str
    source_video: str
    detail: str | None = None


class AvatarVideoSignalingService:
    """Own one authenticated, video-only WebRTC peer and its worker bridge."""

    def __init__(
        self,
        *,
        enabled: bool,
        model: str,
        source_video: Path,
        socket_path: Path,
        fps: int = 25,
        queue_max_ms: int = 500,
        startup_timeout_ms: int = 15000,
        worker_factory: Callable[[], AvatarWorker] | None = None,
    ) -> None:
        self._enabled = enabled
        self._model = model
        self._source_video = source_video
        self._socket_path = socket_path
        self._fps = fps
        self._queue_size = max(2, (fps * queue_max_ms + 999) // 1000)
        self._worker_factory = worker_factory or (
            lambda: UnixSocketAvatarWorker(socket_path, startup_timeout_ms)
        )
        self._worker: AvatarWorker | None = None
        self._track: AvatarVideoTrack | None = None
        self._pc: RTCPeerConnection | None = None
        self._owner_session_id: str | None = None
        self._owner_pc_id = 0
        self._state = "disabled" if not enabled else "unavailable"
        self._detail: str | None = None
        self._lock = asyncio.Lock()

    def status(self) -> AvatarVideoStatus:
        """Return state without exposing the worker socket or filesystem path."""
        state = self._state
        detail = self._detail
        if self._enabled and state in {"ready", "unavailable"}:
            if self._socket_path.is_socket():
                state = "ready"
                detail = None
            else:
                state = "unavailable"
                detail = "MuseTalk renderer is not running."
        return AvatarVideoStatus(
            enabled=self._enabled,
            state=state,
            model=self._model,
            source_video=self._source_video.name,
            detail=detail,
        )

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        """Forward an accepted TTS utterance to the active renderer, if connected."""
        if self._worker is not None:
            await self._worker.begin(utterance_id, sample_rate, emotion)

    async def push_audio(self, utterance_id: str, audio: bytes, timestamp_ns: int) -> None:
        """Forward accepted PCM without making video a prerequisite for audio."""
        if self._worker is not None:
            await self._worker.push_audio(utterance_id, audio, timestamp_ns)

    async def push_event(self, utterance_id: str, event: str) -> None:
        """Forward expressive events to the renderer when the video session is active."""
        if self._worker is not None:
            await self._worker.push_event(utterance_id, event)

    async def cancel(self, utterance_id: str) -> None:
        """Clear renderer state for an interrupted utterance."""
        if self._worker is not None:
            await self._worker.cancel(utterance_id)

    async def end(self, utterance_id: str) -> None:
        """Mark a completed utterance at the renderer boundary."""
        if self._worker is not None:
            await self._worker.end(utterance_id)

    async def offer(self, session_id: str, sdp: str, offer_type: str) -> dict[str, str]:
        """Create the browser-facing video peer after authenticating its owner."""
        if not self._enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Video avatar disabled.",
            )
        if offer_type != "offer":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Offer required.",
            )
        async with self._lock:
            if self._owner_session_id is not None and self._owner_session_id != session_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Avatar session is in use.",
                )
            await self._close_locked()
            worker = self._worker_factory()
            track = AvatarVideoTrack(self._fps, self._queue_size)
            try:
                await worker.start(track.publish)
                pc = RTCPeerConnection()
                pc.addTrack(track)
                await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=offer_type))
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                await self._wait_for_ice(pc)
            except Exception as error:
                await worker.close()
                await track.stop_stream()
                self._state = "error"
                self._detail = str(error)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Avatar video service is unavailable.",
                ) from error
            self._worker = worker
            self._track = track
            self._pc = pc
            self._owner_session_id = session_id
            self._owner_pc_id += 1
            self._state = "connected"
            self._detail = None
            pc.on("connectionstatechange", lambda: asyncio.create_task(self._watch_connection(pc)))
            local = pc.localDescription
            assert local is not None and local.sdp is not None
            return {"sdp": local.sdp, "type": "answer", "pc_id": str(self._owner_pc_id)}

    async def release(self, session_id: str) -> None:
        """Close only the current browser owner's peer and worker stream."""
        async with self._lock:
            self._assert_owner(session_id)
            await self._close_locked()

    async def close(self) -> None:
        """Release resources during application shutdown."""
        async with self._lock:
            await self._close_locked()

    def _assert_owner(self, session_id: str) -> None:
        if self._owner_session_id is None:
            return
        if session_id != self._owner_session_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Avatar session is in use.",
            )

    async def _close_locked(self) -> None:
        if self._track is not None:
            await self._track.stop_stream()
        if self._pc is not None:
            await self._pc.close()
        if self._worker is not None:
            await self._worker.close()
        self._track = None
        self._pc = None
        self._worker = None
        self._owner_session_id = None
        self._state = "disabled" if not self._enabled else "ready"
        self._detail = None

    async def _watch_connection(self, pc: RTCPeerConnection) -> None:
        if pc.connectionState in {"failed", "closed", "disconnected"}:
            async with self._lock:
                if pc is self._pc:
                    await self._close_locked()

    @staticmethod
    async def _wait_for_ice(pc: RTCPeerConnection) -> None:
        if pc.iceGatheringState == "complete":
            return
        event = asyncio.Event()

        @pc.on("icegatheringstatechange")
        def on_state_change() -> None:
            if pc.iceGatheringState == "complete":
                event.set()

        try:
            await asyncio.wait_for(event.wait(), timeout=5)
        except TimeoutError:
            logger.warning("Avatar video ICE gathering timed out; returning gathered candidates.")
