"""Bounded local retrieval boundary for Agent Core context."""

from __future__ import annotations

from typing import Protocol

from arch.rag.embeddings import TextEmbedder
from arch.rag.store import KnowledgeStore


class Retriever(Protocol):
    """Find local document excerpts for one transcription without owning media."""

    def retrieve(self, query: str) -> tuple[str, ...]:
        """Return bounded, source-labeled excerpts."""


class LocalKnowledgeRetriever:
    """Embed one transcript and return source-labeled excerpts from the local index."""

    def __init__(self, store: KnowledgeStore, embedder: TextEmbedder, limit: int = 4) -> None:
        if limit < 1:
            raise ValueError("Knowledge retrieval limit must be positive.")
        self._store = store
        self._embedder = embedder
        self._limit = limit

    def retrieve(self, query: str) -> tuple[str, ...]:
        """Return only a small, inspectable set of non-empty document excerpts."""
        normalized = query.strip()
        if not normalized:
            return ()
        query_vector = self._embedder.encode_query(normalized)
        return tuple(
            f"来源：{hit.path}\n{hit.content}"
            for hit in self._store.search_hybrid(query_vector, normalized, limit=self._limit)
            if hit.content
        )
