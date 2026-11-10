"""Tests for strict utterance cancellation across direct and future avatar output."""

import asyncio

from arch.avatar.adapter import AvatarCall, MockAvatarAdapter
from arch.voice.cancellation import CancellationController
from arch.voice.output_mux import OutputMode, OutputMux


def test_cancelled_utterance_cannot_emit_late_direct_audio_or_events() -> None:
    """A replacement utterance receives output while delayed old callbacks are dropped."""
    direct = MockAvatarAdapter()
    mux = OutputMux(CancellationController(), direct, MockAvatarAdapter())

    async def exercise() -> None:
        await mux.begin("old", 24000)
        assert await mux.push_audio("old", b"old", 1)
        await mux.begin("new", 24000)
        assert not await mux.push_audio("old", b"late", 2)
        assert not await mux.push_event("old", "late-event")
        assert await mux.push_audio("new", b"new", 3)
        assert await mux.end("new")

    asyncio.run(exercise())

    assert direct.calls == [
        AvatarCall("begin", "old", "24000:neutral"),
        AvatarCall("audio", "old", "1"),
        AvatarCall("cancel", "old"),
        AvatarCall("begin", "new", "24000:neutral"),
        AvatarCall("audio", "new", "3"),
        AvatarCall("end", "new"),
    ]


def test_avatar_mode_owns_output_and_cancellation() -> None:
    """Avatar mode does not duplicate PCM on the direct browser output path."""
    direct = MockAvatarAdapter()
    avatar = MockAvatarAdapter()
    mux = OutputMux(CancellationController(), direct, avatar, mode=OutputMode.AVATAR_AV)

    async def exercise() -> None:
        await mux.begin("avatar", 24000, "calm")
        assert await mux.push_audio("avatar", b"pcm", 10)
        assert await mux.push_event("avatar", "smile")
        assert await mux.cancel("avatar")
        assert not await mux.end("avatar")

    asyncio.run(exercise())

    assert direct.calls == []
    assert avatar.calls == [
        AvatarCall("begin", "avatar", "24000:calm"),
        AvatarCall("audio", "avatar", "10"),
        AvatarCall("event", "avatar", "smile"),
        AvatarCall("cancel", "avatar"),
    ]
