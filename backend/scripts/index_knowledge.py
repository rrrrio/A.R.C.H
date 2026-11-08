"""Explicitly rebuild the local RAG index from the bounded knowledge inbox."""

from __future__ import annotations

from local_voice_agent.rag.embeddings import SentenceTransformerEmbedder
from local_voice_agent.rag.indexer import KnowledgeIndexer
from local_voice_agent.rag.store import KnowledgeStore
from local_voice_agent.settings import get_settings


def main() -> None:
    """Require a prepared local model, then synchronize inbox files into SQLite."""
    settings = get_settings()
    settings.ensure_runtime_directories()
    if not settings.rag_model_path.is_dir():
        raise SystemExit("BGE-M3 is missing; run make rag-prepare before indexing knowledge.")
    store = KnowledgeStore(settings.database_path)
    store.migrate()
    indexer = KnowledgeIndexer(
        settings.knowledge_inbox_path,
        store,
        SentenceTransformerEmbedder(str(settings.rag_model_path)),
    )
    print(f"Indexed {indexer.rebuild()} changed source(s).")


if __name__ == "__main__":
    main()
