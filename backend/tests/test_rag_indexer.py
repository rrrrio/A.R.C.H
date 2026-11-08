"""Tests for incremental, local knowledge indexing."""

from collections.abc import Sequence
from pathlib import Path

from local_voice_agent.rag.indexer import KnowledgeIndexer
from local_voice_agent.rag.store import KnowledgeStore


class FakeEmbedder:
    """Use deterministic vectors without downloading a real embedding model in tests."""

    def encode_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple((float(len(text)), 1.0) for text in texts)

    def encode_query(self, text: str) -> tuple[float, ...]:
        return (float(len(text)), 1.0)


def test_indexer_indexes_changes_and_removes_deleted_inbox_files(tmp_path: Path) -> None:
    """Only changed supported files are embedded, and deletion removes their indexed chunks."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    note = inbox / "note.txt"
    note.write_text("本地资料", encoding="utf-8")
    store = KnowledgeStore(tmp_path / "voice.sqlite3")
    store.migrate()
    indexer = KnowledgeIndexer(inbox, store, FakeEmbedder())

    assert indexer.rebuild() == 1
    assert indexer.rebuild() == 0
    assert store.search((4.0, 1.0))[0].path == "note.txt"

    note.unlink()
    assert indexer.rebuild() == 0
    assert store.search((4.0, 1.0)) == ()
