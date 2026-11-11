"""Tests for relevance-bounded local knowledge retrieval."""

from collections.abc import Sequence
from pathlib import Path

from arch.rag.retriever import LocalKnowledgeRetriever
from arch.rag.store import KnowledgeStore


class FixedEmbedder:
    """Return one stable query vector without loading a model."""

    def encode_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0) for _ in texts)

    def encode_query(self, text: str) -> tuple[float, ...]:
        assert text == "看一下周报"
        return (1.0, 0.0)


def test_retriever_excludes_low_relevance_chunks_from_agent_context(tmp_path: Path) -> None:
    """A generic low-score chunk cannot suppress tools for an unrelated request."""
    store = KnowledgeStore(tmp_path / "knowledge.sqlite3")
    store.migrate()
    store.replace_source("weekly-report.txt", "weekly", (("周报已完成事项", (1.0, 0.0)),))
    store.replace_source("unrelated.txt", "unrelated", (("无关资料", (0.3, 0.95)),))

    context = LocalKnowledgeRetriever(store, FixedEmbedder()).retrieve("看一下周报")

    assert context == ("来源：weekly-report.txt\n周报已完成事项",)
