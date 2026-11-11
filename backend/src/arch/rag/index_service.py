"""Application-owned, serialized rebuilds of the configured local knowledge index."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Protocol

from arch.rag.embeddings import SentenceTransformerEmbedder
from arch.rag.indexer import KnowledgeIndexer
from arch.rag.store import KnowledgeStore


class KnowledgeIndexService(Protocol):
    """Rebuild the fixed inbox index without accepting browser-controlled paths."""

    def rebuild(self) -> int:
        """Synchronize the configured inbox and return the changed source count."""


class KnowledgeIndexBusyError(RuntimeError):
    """Raised when a previous browser-triggered rebuild is still running."""


class KnowledgeIndexUnavailableError(RuntimeError):
    """Raised when the prepared local embedding model cannot be loaded."""


class LocalKnowledgeIndexService:
    """Lazily load one prepared embedder and serialize rebuilds for one application."""

    def __init__(self, inbox: Path, model_path: Path, store: KnowledgeStore) -> None:
        self._inbox = inbox
        self._model_path = model_path
        self._store = store
        self._embedder: SentenceTransformerEmbedder | None = None
        self._lock = Lock()

    def rebuild(self) -> int:
        """Run the same bounded incremental rebuild as the operator command."""
        if not self._lock.acquire(blocking=False):
            raise KnowledgeIndexBusyError
        try:
            if not self._model_path.is_dir():
                raise KnowledgeIndexUnavailableError("Local RAG model is not ready.")
            if self._embedder is None:
                self._embedder = SentenceTransformerEmbedder(str(self._model_path))
            return KnowledgeIndexer(self._inbox, self._store, self._embedder).rebuild()
        finally:
            self._lock.release()
