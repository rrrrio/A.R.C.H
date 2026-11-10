"""Explicit all-local rebuild of the user knowledge inbox index."""

from __future__ import annotations

from pathlib import Path

from arch.rag.embeddings import TextEmbedder
from arch.rag.ingest import chunk_text, scan_inbox
from arch.rag.store import KnowledgeStore


class KnowledgeIndexer:
    """Index only changed inbox sources and remove records for deleted sources."""

    def __init__(self, inbox: Path, store: KnowledgeStore, embedder: TextEmbedder) -> None:
        self._inbox = inbox
        self._store = store
        self._embedder = embedder

    def rebuild(self) -> int:
        """Synchronize local SQLite vectors to the bounded inbox and return changed source count."""
        documents = scan_inbox(self._inbox)
        paths = {document.path for document in documents}
        changed = 0
        for document in documents:
            if self._store.source_is_current(document.path, document.digest):
                continue
            chunks = chunk_text(document.text)
            vectors = self._embedder.encode_documents(chunks)
            if len(chunks) != len(vectors):
                raise RuntimeError("Embedding model returned a mismatched vector count.")
            self._store.replace_source(
                document.path, document.digest, tuple(zip(chunks, vectors, strict=True))
            )
            changed += 1
        self._store.remove_sources_except(paths)
        return changed
