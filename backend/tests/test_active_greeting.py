"""Tests for the opt-in idle greeting controller."""

import asyncio

import pytest

from arch.voice.active_greeting import ActiveGreetingController, ActiveGreetingPolicy


def test_active_greeting_policy_rejects_unbounded_idle_times() -> None:
    with pytest.raises(ValueError):
        ActiveGreetingPolicy(enabled=True, minimum_idle_seconds=299)
    with pytest.raises(ValueError):
        ActiveGreetingPolicy(enabled=True, minimum_idle_seconds=3601)


def test_disabled_active_greeting_never_creates_a_background_timer() -> None:
    async def exercise() -> None:
        greeted = False

        async def greet() -> None:
            nonlocal greeted
            greeted = True

        controller = ActiveGreetingController(ActiveGreetingPolicy(), greet)
        controller.arm()
        await asyncio.sleep(0)
        await controller.close()
        assert not greeted

    asyncio.run(exercise())


def test_active_greeting_rearms_after_each_idle_interval_without_a_daily_cap() -> None:
    async def exercise() -> None:
        ticks: asyncio.Queue[None] = asyncio.Queue()
        greeted = 0
        twice_greeted = asyncio.Event()

        async def sleeper(_: float) -> None:
            await ticks.get()

        async def greet() -> None:
            nonlocal greeted
            greeted += 1
            if greeted == 2:
                twice_greeted.set()

        controller = ActiveGreetingController(
            ActiveGreetingPolicy(enabled=True), greet, sleeper=sleeper
        )
        controller.arm()
        await ticks.put(None)
        await asyncio.sleep(0)
        await ticks.put(None)
        await asyncio.wait_for(twice_greeted.wait(), timeout=1)
        await controller.close()
        assert greeted == 2

    asyncio.run(exercise())


def test_activity_cancels_the_current_idle_wait() -> None:
    async def exercise() -> None:
        entered_wait = asyncio.Event()
        cancelled = asyncio.Event()
        block = asyncio.Event()

        async def sleeper(_: float) -> None:
            entered_wait.set()
            try:
                await block.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def greet() -> None:
            raise AssertionError("A reset idle timer must not greet.")

        controller = ActiveGreetingController(
            ActiveGreetingPolicy(enabled=True), greet, sleeper=sleeper
        )
        controller.arm()
        await asyncio.wait_for(entered_wait.wait(), timeout=1)
        controller.activity()
        await asyncio.wait_for(cancelled.wait(), timeout=1)
        await controller.close()

    asyncio.run(exercise())
