"""Bounds for memory that may enter a local language-model prompt."""

from __future__ import annotations

from collections.abc import Iterable

MAX_MEMORY_CANDIDATE_CHARS = 320
MAX_APPROVED_MEMORY_COUNT = 12
MAX_APPROVED_MEMORY_CONTEXT_CHARS = 2000


def bounded_approved_memories(contents: Iterable[str]) -> tuple[str, ...]:
    """Keep the newest valid entries that fit both count and aggregate prompt budgets."""
    selected: list[str] = []
    total_chars = 0
    for content in contents:
        if not content or len(content) > MAX_MEMORY_CANDIDATE_CHARS:
            continue
        if len(selected) >= MAX_APPROVED_MEMORY_COUNT:
            break
        if total_chars + len(content) > MAX_APPROVED_MEMORY_CONTEXT_CHARS:
            continue
        selected.append(content)
        total_chars += len(content)
    return tuple(selected)
