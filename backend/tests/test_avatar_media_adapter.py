"""Whole-answer avatar media adapter regressions."""

from __future__ import annotations

import asyncio

from arch.avatar.media import AvatarMediaAnswer, AvatarMediaService
from arch.avatar.media_adapter import AvatarMediaAdapter


class Runtime:
    def __init__(self, *, fail_submit: bool = False) -> None:
        self.fail_submit = fail_submit
        self.submitted: tuple[str, str, bytes] | None = None
        self.cancelled: tuple[str, str] | None = None

    async def create_session(
        self, *, ticket: str, session_id: str, offer_sdp: str
    ) -> AvatarMediaAnswer:
        del ticket, session_id, offer_sdp
        return AvatarMediaAnswer(sdp="answer", pc_id="peer")

    async def close_session(self, *, session_id: str) -> None:
        del session_id

    async def submit_utterance(
        self, *, session_id: str, utterance_id: str, pcm_24khz: bytes
    ) -> None:
        if self.fail_submit:
            raise RuntimeError("synthetic runtime failure")
        self.submitted = (session_id, utterance_id, pcm_24khz)

    async def cancel_utterance(self, *, session_id: str, utterance_id: str) -> None:
        self.cancelled = (session_id, utterance_id)


def test_adapter_submits_one_complete_24khz_answer_for_the_active_session() -> None:
    runtime = Runtime()
    service = AvatarMediaService(runtime=runtime)
    adapter = AvatarMediaAdapter(service, "session")

    async def exercise() -> bool:
        await service.offer("session", "offer")
        await adapter.begin("answer", 24_000, "neutral")
        await adapter.push_audio("answer", b"first", 1)
        await adapter.push_audio("answer", b"second", 2)
        return await adapter.end("answer")

    assert asyncio.run(exercise())
    assert runtime.submitted == ("session", "answer", b"firstsecond")


def test_adapter_returns_false_for_same_frame_direct_audio_fallback() -> None:
    runtime = Runtime(fail_submit=True)
    service = AvatarMediaService(runtime=runtime)
    adapter = AvatarMediaAdapter(service, "session")

    async def exercise() -> bool:
        await service.offer("session", "offer")
        await adapter.begin("answer", 24_000, "neutral")
        await adapter.push_audio("answer", b"pcm", 1)
        return await adapter.end("answer")

    assert not asyncio.run(exercise())


def test_adapter_cancellation_fences_unsubmitted_and_runtime_audio() -> None:
    runtime = Runtime()
    service = AvatarMediaService(runtime=runtime)
    adapter = AvatarMediaAdapter(service, "session")

    async def exercise() -> None:
        await service.offer("session", "offer")
        await adapter.begin("answer", 24_000, "neutral")
        await adapter.push_audio("answer", b"pcm", 1)
        await adapter.cancel("answer")

    asyncio.run(exercise())
    assert runtime.submitted is None
    assert runtime.cancelled == ("session", "answer")
