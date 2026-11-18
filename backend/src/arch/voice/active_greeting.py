"""Session-scoped, opt-in idle greeting eligibility controller."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True, slots=True)
class ActiveGreetingPolicy:
    enabled: bool = False
    minimum_idle_seconds: float = 600.0

    def __post_init__(self) -> None:
        if not 300.0 <= self.minimum_idle_seconds <= 3600.0:
            raise ValueError("Minimum idle time must be between 5 and 60 minutes.")


class ActiveGreetingController:
    """Own exactly one idle timer and cancel it whenever ordinary conversation resumes."""

    def __init__(
        self,
        policy: ActiveGreetingPolicy,
        greet: Callable[[], Awaitable[object]],
        can_greet: Callable[[], Awaitable[bool]] | None = None,
        *,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._policy = policy
        self._greet = greet
        self._can_greet = can_greet
        self._clock = clock
        self._sleeper = sleeper
        self._last_activity = clock()
        self._closed = False
        self._task: asyncio.Task[None] | None = None
        self._speaking = False
        self._approval_pending = False
        self._activity_generation = 0

    def arm(self) -> None:
        """Begin waiting only after the media session is ready."""
        self._schedule()

    def activity(self) -> None:
        """Reset eligibility for speech, a final transcript, or an assistant response."""
        self._last_activity = self._clock()
        self._activity_generation += 1
        self._cancel()
        self._schedule()

    def set_speaking(self, speaking: bool) -> None:
        self._speaking = speaking
        self.activity()

    def set_approval_pending(self, pending: bool) -> None:
        self._approval_pending = pending
        self.activity()

    async def close(self) -> None:
        self._closed = True
        task = self._task
        self._cancel()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _schedule(self) -> None:
        eligible = not self._closed and self._policy.enabled
        eligible = eligible and not self._speaking and not self._approval_pending
        if eligible:
            self._task = asyncio.create_task(self._wait_and_greet())

    def _cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _wait_and_greet(self) -> None:
        try:
            deadline = self._last_activity + self._policy.minimum_idle_seconds
            await self._sleeper(max(0.0, deadline - self._clock()))
            allowed = self._can_greet is None or await self._can_greet()
            if allowed and not self._closed and not self._speaking and not self._approval_pending:
                activity_generation = self._activity_generation
                await self._greet()
                if activity_generation == self._activity_generation:
                    self._last_activity = self._clock()
        except asyncio.CancelledError:
            raise
        finally:
            if self._task is asyncio.current_task():
                self._task = None
                self._schedule()
