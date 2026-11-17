"""Backend-neutral media contract checks."""

from __future__ import annotations

import av

from avatar_media_runtime.contracts import PreparedMedia


def test_prepared_media_retains_24khz_pcm_and_frame_sequence_in_memory() -> None:
    frame = av.VideoFrame(width=8, height=8, format="rgb24")
    prepared = PreparedMedia("turn-1", b"\x00\x00" * 24_000, [frame], 25)

    assert prepared.utterance_id == "turn-1"
    assert len(prepared.audio_pcm_24khz) == 48_000
    assert prepared.video_frames[0] is frame
    assert prepared.fps == 25
