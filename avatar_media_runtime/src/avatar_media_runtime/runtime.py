"""In-memory single-peer session owner for prepared avatar media."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from hashlib import sha256

from aiortc import RTCPeerConnection, RTCSessionDescription

from avatar_media_runtime.contracts import LipSyncBackend
from avatar_media_runtime.media import SessionMedia
from avatar_media_runtime.tickets import MediaTicket

logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True, slots=True)
class Answer:
    """The single peer's SDP answer and opaque runtime session identifier."""

    session_id: str
    sdp: str


class MediaPreparationError(RuntimeError):
    """A renderer failure that callers can route to ordinary-audio fallback."""

    def __init__(self, utterance_id: str) -> None:
        super().__init__("Avatar media preparation failed.")
        self.utterance_id = utterance_id


class AvatarMediaRuntime:
    """Own one audio/video peer per authenticated browser session."""

    def __init__(
        self, backend: LipSyncBackend | None = None, *, disconnect_grace_seconds: float = 5.0
    ) -> None:
        self._backend = backend
        self._sessions: dict[str, tuple[RTCPeerConnection, SessionMedia]] = {}
        self._cancelled_utterances: set[tuple[str, str]] = set()
        self._disconnect_grace_seconds = disconnect_grace_seconds
        self._disconnect_cleanup_tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    def is_ready(self) -> bool:
        """Report whether this process can prepare media, not merely accept HTTP requests."""
        return self._backend is not None

    async def create_session(self, ticket: MediaTicket, offer_sdp: str) -> Answer:
        """Negotiate one peer carrying deterministic audio and video fixtures."""
        async with self._lock:
            if self._sessions:
                raise RuntimeError("Avatar media session already exists.")
            media = SessionMedia()
            peer = RTCPeerConnection()
            try:
                peer.addTrack(media.audio)
                peer.addTrack(media.video)
                await peer.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))
                answer = await peer.createAnswer()
                await peer.setLocalDescription(answer)
                await self._wait_for_ice(peer)
                local = peer.localDescription
                assert local is not None and local.sdp is not None
            except Exception:
                await peer.close()
                raise
            self._sessions[ticket.session_id] = (peer, media)

            @peer.on("connectionstatechange")
            def release_disconnected_peer() -> None:
                self._handle_peer_state(ticket.session_id, peer)

            return Answer(ticket.session_id, local.sdp)

    async def submit(self, session_id: str, utterance_id: str, pcm_24khz: bytes) -> None:
        """Prepare one completed utterance and publish it to the existing peer tracks."""
        if self._backend is None:
            raise RuntimeError("Avatar media backend is not configured.")
        _log_timing(session_id, utterance_id, "runtime-submission")
        backend_utterance_id = self._backend_utterance_id(session_id, utterance_id)
        try:
            prepared = await self._backend.prepare(pcm_24khz, backend_utterance_id)
        except Exception as error:
            raise MediaPreparationError(utterance_id) from error
        _log_timing(session_id, utterance_id, "render-complete")
        async with self._lock:
            if (session_id, utterance_id) in self._cancelled_utterances:
                raise MediaPreparationError(utterance_id)
            pair = self._sessions.get(session_id)
            if pair is None:
                raise RuntimeError("Avatar media session does not exist.")
            await pair[1].publish(
                session_id,
                utterance_id,
                prepared.audio_pcm_24khz,
                list(prepared.video_frames),
                prepared.fps,
            )

    async def cancel(self, session_id: str, utterance_id: str) -> None:
        """Stop both media tracks for a session's prepared turn."""
        async with self._lock:
            self._cancelled_utterances.add((session_id, utterance_id))
            pair = self._sessions.get(session_id)
            if pair is not None:
                pair[1].cancel()
        if self._backend is not None:
            await self._backend.cancel(self._backend_utterance_id(session_id, utterance_id))
        _log_timing(session_id, utterance_id, "cancellation-complete")

    async def close_session(self, session_id: str) -> None:
        """Release the peer and all prepared in-memory media."""
        self._cancel_disconnect_cleanup(session_id)
        async with self._lock:
            pair = self._sessions.pop(session_id, None)
            self._cancelled_utterances = {
                key for key in self._cancelled_utterances if key[0] != session_id
            }
        if pair is not None:
            pair[1].cancel()
            await pair[0].close()

    def _handle_peer_state(self, session_id: str, peer: RTCPeerConnection) -> None:
        """Free terminal peers while permitting a short browser network recovery window."""
        if peer.connectionState in {"closed", "failed"}:
            self._cancel_disconnect_cleanup(session_id)
            asyncio.create_task(self._release_disconnected_peer(session_id, peer))
        elif peer.connectionState == "disconnected":
            if session_id not in self._disconnect_cleanup_tasks:
                self._disconnect_cleanup_tasks[session_id] = asyncio.create_task(
                    self._release_after_disconnect_grace(session_id, peer)
                )
        elif peer.connectionState == "connected":
            self._cancel_disconnect_cleanup(session_id)

    def _cancel_disconnect_cleanup(self, session_id: str) -> None:
        task = self._disconnect_cleanup_tasks.pop(session_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _release_after_disconnect_grace(
        self, session_id: str, peer: RTCPeerConnection
    ) -> None:
        """Release only a peer that remains disconnected for the full recovery window."""
        try:
            await asyncio.sleep(self._disconnect_grace_seconds)
            if peer.connectionState == "disconnected":
                await self._release_disconnected_peer(session_id, peer)
        finally:
            task = self._disconnect_cleanup_tasks.get(session_id)
            if task is asyncio.current_task():
                self._disconnect_cleanup_tasks.pop(session_id, None)

    async def _release_disconnected_peer(
        self, session_id: str, peer: RTCPeerConnection
    ) -> None:
        """Discard only the matching peer after aiortc observes its terminal state."""
        self._cancel_disconnect_cleanup(session_id)
        async with self._lock:
            pair = self._sessions.get(session_id)
            if pair is None or pair[0] is not peer:
                return
            self._sessions.pop(session_id)
            self._cancelled_utterances = {
                key for key in self._cancelled_utterances if key[0] != session_id
            }
        pair[1].cancel()
        if peer.connectionState != "closed":
            await peer.close()

    @staticmethod
    async def _wait_for_ice(peer: RTCPeerConnection) -> None:
        if peer.iceGatheringState == "complete":
            return
        complete = asyncio.Event()

        @peer.on("icegatheringstatechange")
        def gathered() -> None:
            if peer.iceGatheringState == "complete":
                complete.set()

        await asyncio.wait_for(complete.wait(), timeout=5)

    @staticmethod
    def _backend_utterance_id(session_id: str, utterance_id: str) -> str:
        """Keep renderer cancellation state private and unique across browser sessions."""
        return sha256(f"{session_id}\0{utterance_id}".encode()).hexdigest()


def _log_timing(session_id: str, utterance_id: str, stage: str) -> None:
    """Emit timing metadata without retaining media payloads."""
    logger.info(
        "%s",
        json.dumps(
            {
                "event": "avatar-media-timing",
                "monotonic_ns": time.monotonic_ns(),
                "session_id": session_id,
                "stage": stage,
                "utterance_id": utterance_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    )
