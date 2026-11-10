"""Bounded expressive labels shared by agent, voice, and avatar boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pipecat.frames.frames import Frame

type Emotion = Literal["neutral", "warm", "calm", "urgent"]
DEFAULT_EMOTION: Emotion = "neutral"
ALLOWED_EMOTIONS = frozenset[Emotion]({"neutral", "warm", "calm", "urgent"})


def normalize_emotion(value: str) -> Emotion:
    """Map untrusted agent text to the only expressive labels the output layer accepts."""
    candidate = value.strip().lower()
    return candidate if candidate in ALLOWED_EMOTIONS else DEFAULT_EMOTION


@dataclass
class EmotionFrame(Frame):
    """Carry one normalized output expression without exposing model-specific controls."""

    emotion: Emotion = DEFAULT_EMOTION
