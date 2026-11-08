"""Mutually exclusive direct-audio and future avatar audio/video output routing."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from local_voice_agent.avatar.adapter import AvatarAdapter
from local_voice_agent.voice.cancellation import CancellationController


class OutputMode(StrEnum):
    """The one permitted output owner for an active voice utterance."""

    DIRECT_AUDIO = "direct_audio"
    AVATAR_AV = "avatar_av"


class DirectAudioOutput(Protocol):
    """Target for browser-bound PCM and events while no avatar owns output."""

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        """Start a browser audio utterance."""

    async def push_audio(self, utterance_id: str, audio: bytes, timestamp_ns: int) -> None:
        """Send a timestamped PCM chunk to the browser output."""

    async def push_event(self, utterance_id: str, event: str) -> None:
        """Send an expressive event to the browser output."""

    async def cancel(self, utterance_id: str) -> None:
        """Discard browser-bound buffered output."""

    async def end(self, utterance_id: str) -> None:
        """Finish browser-bound output normally."""


class OutputMux:
    """Route active output through exactly one adapter and suppress stale callbacks."""

    def __init__(
        self,
        cancellation: CancellationController,
        direct_audio: DirectAudioOutput,
        avatar: AvatarAdapter,
        mode: OutputMode = OutputMode.DIRECT_AUDIO,
    ) -> None:
        self._cancellation = cancellation
        self._direct_audio = direct_audio
        self._avatar = avatar
        self._mode = mode

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str = "neutral") -> None:
        """Replace any old output before starting the requested utterance."""
        previous = self._cancellation.activate(utterance_id)
        if previous is not None:
            await self._output().cancel(previous)
        await self._output().begin(utterance_id, sample_rate, emotion)

    async def push_audio(self, utterance_id: str, audio: bytes, timestamp_ns: int) -> bool:
        """Forward PCM only while its utterance remains active."""
        if not self._cancellation.accepts(utterance_id):
            return False
        await self._output().push_audio(utterance_id, audio, timestamp_ns)
        return True

    async def push_event(self, utterance_id: str, event: str) -> bool:
        """Forward expressive events only while their utterance remains active."""
        if not self._cancellation.accepts(utterance_id):
            return False
        await self._output().push_event(utterance_id, event)
        return True

    async def cancel(self, utterance_id: str) -> bool:
        """Cancel active output and reject repeated or stale cancellation calls."""
        if not self._cancellation.cancel(utterance_id):
            return False
        await self._output().cancel(utterance_id)
        return True

    async def end(self, utterance_id: str) -> bool:
        """Finish active output and reject delayed completion callbacks."""
        if not self._cancellation.finish(utterance_id):
            return False
        await self._output().end(utterance_id)
        return True

    def _output(self) -> DirectAudioOutput | AvatarAdapter:
        return self._direct_audio if self._mode == OutputMode.DIRECT_AUDIO else self._avatar
