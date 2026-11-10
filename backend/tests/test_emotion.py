"""Tests for the bounded expression vocabulary at the media boundary."""

from arch.voice.emotion import normalize_emotion


def test_normalize_emotion_accepts_only_the_declared_labels() -> None:
    """Agent text cannot become an arbitrary voice or avatar runtime control."""
    assert normalize_emotion(" WARM ") == "warm"
    assert normalize_emotion("urgent") == "urgent"
    assert normalize_emotion("<untrusted-style-parameter>") == "neutral"
