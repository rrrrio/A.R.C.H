"""Tests for bounded approved-memory prompt context."""

from local_voice_agent.memory.context import (
    MAX_APPROVED_MEMORY_CONTEXT_CHARS,
    MAX_APPROVED_MEMORY_COUNT,
    MAX_MEMORY_CANDIDATE_CHARS,
    bounded_approved_memories,
)


def test_bounded_approved_memories_enforces_count_and_total_character_budgets() -> None:
    """Prompt context keeps only valid entries that fit the shared aggregate budget."""
    contents = tuple("x" * MAX_MEMORY_CANDIDATE_CHARS for _ in range(20))

    bounded = bounded_approved_memories(contents)

    assert len(bounded) <= MAX_APPROVED_MEMORY_COUNT
    assert sum(len(content) for content in bounded) <= MAX_APPROVED_MEMORY_CONTEXT_CHARS
    assert bounded == contents[:6]


def test_bounded_approved_memories_skips_individually_oversized_entries() -> None:
    """No direct AgentRequest caller can bypass the candidate-length boundary."""
    assert bounded_approved_memories(("x" * (MAX_MEMORY_CANDIDATE_CHARS + 1), "保留")) == (
        "保留",
    )
