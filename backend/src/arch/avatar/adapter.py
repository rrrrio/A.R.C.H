"""Avatar output contracts and deterministic test adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AvatarAdapter(Protocol):
    """Receive one active utterance's synchronized audio and expressive events."""

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        """Start rendering an utterance."""

    async def push_audio(self, utterance_id: str, audio: bytes, timestamp_ns: int) -> None:
        """Consume a timestamped PCM chunk for the active utterance."""

    async def push_event(self, utterance_id: str, event: str) -> None:
        """Consume an expressive event for the active utterance."""

    async def cancel(self, utterance_id: str) -> None:
        """Discard buffered output for an interrupted utterance."""

    async def end(self, utterance_id: str) -> bool | None:
        """Finish an utterance that completed normally."""


class NullAvatarAdapter:
    """Intentional no-op while direct browser audio is the active output mode."""

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        """Accept a begin signal without allocating rendering resources."""

    async def push_audio(self, utterance_id: str, audio: bytes, timestamp_ns: int) -> None:
        """Discard direct-audio mode PCM."""

    async def push_event(self, utterance_id: str, event: str) -> None:
        """Discard direct-audio mode events."""

    async def cancel(self, utterance_id: str) -> None:
        """Accept a cancellation without side effects."""

    async def end(self, utterance_id: str) -> None:
        """Accept normal completion without side effects."""


@dataclass(frozen=True, slots=True)
class AvatarCall:
    """One call recorded by ``MockAvatarAdapter`` for deterministic tests."""

    kind: str
    utterance_id: str
    payload: str = ""


class MockAvatarAdapter:
    """Record adapter calls without requiring an actual avatar runtime."""

    def __init__(self) -> None:
        self.calls: list[AvatarCall] = []

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        """Record output initialization."""
        self.calls.append(AvatarCall("begin", utterance_id, f"{sample_rate}:{emotion}"))

    async def push_audio(self, utterance_id: str, audio: bytes, timestamp_ns: int) -> None:
        """Record an audio chunk without retaining PCM."""
        self.calls.append(AvatarCall("audio", utterance_id, str(timestamp_ns)))

    async def push_event(self, utterance_id: str, event: str) -> None:
        """Record an expressive event."""
        self.calls.append(AvatarCall("event", utterance_id, event))

    async def cancel(self, utterance_id: str) -> None:
        """Record cancellation."""
        self.calls.append(AvatarCall("cancel", utterance_id))

    async def end(self, utterance_id: str) -> None:
        """Record normal completion."""
        self.calls.append(AvatarCall("end", utterance_id))
