"""Explicit local embedding-model boundary for knowledge indexing and retrieval."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, cast


class TextEmbedder(Protocol):
    """Encode local documents and queries into comparable dense vectors."""

    def encode_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """Return one normalized vector per source chunk."""

    def encode_query(self, text: str) -> tuple[float, ...]:
        """Return one normalized vector for a user query."""


class SentenceTransformerEmbedder:
    """Load BGE-M3 only from a prepared local snapshot directory."""

    def __init__(self, model_path: str) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_path, local_files_only=True, device="cuda")

    def encode_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        vectors = cast(
            Sequence[Sequence[float]],
            self._model.encode_document(list(texts), normalize_embeddings=True),
        )
        return tuple(tuple(float(value) for value in vector) for vector in vectors)

    def encode_query(self, text: str) -> tuple[float, ...]:
        vector = cast(Sequence[float], self._model.encode_query(text, normalize_embeddings=True))
        return tuple(float(value) for value in vector)
