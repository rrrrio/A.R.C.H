"""Tests for the rebuildable SQLite knowledge vector index."""

from pathlib import Path

from arch.rag.store import KnowledgeStore


def test_knowledge_store_replaces_sources_searches_vectors_and_removes_missing_paths(
    tmp_path: Path,
) -> None:
    """Source replacement and deletion keep the local index consistent with the inbox."""
    store = KnowledgeStore(tmp_path / "voice.sqlite3")
    assert store.migrate() == (1, 2, 3, 4, 5, 6, 7, 8, 9)
    store.replace_source(
        "notes.md",
        "digest-1",
        (("第一段", (1.0, 0.0)), ("第二段", (0.0, 1.0))),
    )
    store.replace_source("other.txt", "digest-2", (("第三段", (0.8, 0.2)),))

    assert [hit.content for hit in store.search((1.0, 0.0), limit=2)] == ["第一段", "第三段"]
    assert store.source_is_current("notes.md", "digest-1")

    store.replace_source("notes.md", "digest-3", (("替换段", (0.0, 1.0)),))
    store.remove_sources_except({"notes.md"})

    assert [hit.content for hit in store.search((0.0, 1.0))] == ["替换段"]
    assert not store.source_is_current("other.txt", "digest-2")


def test_knowledge_store_hybrid_search_promotes_matching_keywords_and_reports_status(
    tmp_path: Path,
) -> None:
    """Keyword overlap supplements vector similarity without a separate search service."""
    store = KnowledgeStore(tmp_path / "voice.sqlite3")
    store.migrate()
    store.replace_source("unrelated.md", "digest-1", (("无关资料", (1.0, 0.0)),))
    store.replace_source("weather.md", "digest-2", (("北京天气预报说明", (0.7, 0.7)),))

    hits = store.search_hybrid((1.0, 0.0), "北京天气", limit=2)
    count, newest = store.index_status()

    assert [hit.path for hit in hits] == ["weather.md", "unrelated.md"]
    assert count == 2
    assert newest is not None
