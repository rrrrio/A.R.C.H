"""Backend-neutral prepared-media contracts for the avatar runtime."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import av


@dataclass(frozen=True, slots=True)
class PreparedMedia:
    """A completed in-memory utterance and its speaking frames."""

    utterance_id: str
    audio_pcm_24khz: bytes
    video_frames: Sequence[av.VideoFrame]
    fps: int


class LipSyncBackend(Protocol):
    """Produce prepared frames without browser, ticket, or WebRTC knowledge."""

    async def prepare(self, pcm_24khz: bytes, utterance_id: str) -> PreparedMedia:
        """Prepare synchronized media entirely in memory."""

    async def cancel(self, utterance_id: str) -> None:
        """Fence any in-progress preparation for an interrupted utterance."""
