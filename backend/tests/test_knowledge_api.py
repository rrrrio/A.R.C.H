"""Tests for the authenticated local knowledge-index status endpoint."""

from pathlib import Path

from fastapi.testclient import TestClient

from arch.auth.credentials import provision_credentials
from arch.auth.service import AuthService
from arch.main import create_app
from arch.rag.store import KnowledgeStore

PUBLIC_ORIGIN = "https://voice.test:8443"


class FakeKnowledgeIndexService:
    """Record one browser-requested rebuild without loading an embedding model."""

    def __init__(self, changed: int = 0) -> None:
        self.changed = changed
        self.calls = 0

    def rebuild(self) -> int:
        self.calls += 1
        return self.changed


def test_knowledge_status_requires_login_and_does_not_expose_source_paths(tmp_path: Path) -> None:
    """The browser can see index freshness after login without receiving document metadata."""
    credential_path = tmp_path / "admin.json"
    provision_credentials(credential_path, "correct-horse-battery-staple")
    store = KnowledgeStore(tmp_path / "voice.sqlite3")
    store.migrate()
    store.replace_source("private/notes.md", "digest", (("用户资料", (1.0, 0.0)),))
    app = create_app(AuthService(credential_path, PUBLIC_ORIGIN), knowledge_store=store)

    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        anonymous = client.get("/api/knowledge/status")
        client.post(
            "/api/auth/login",
            headers={"Origin": PUBLIC_ORIGIN},
            json={"password": "correct-horse-battery-staple"},
        )
        authenticated = client.get("/api/knowledge/status")

    assert anonymous.status_code == 401
    assert authenticated.status_code == 200
    assert authenticated.json()["source_count"] == 1
    assert authenticated.json()["indexed_at"] is not None
    assert "private" not in authenticated.text


def test_knowledge_reindex_requires_csrf_and_returns_refreshed_safe_status(tmp_path: Path) -> None:
    """The administrator can rebuild only the configured inbox through the browser flow."""
    credential_path = tmp_path / "admin.json"
    provision_credentials(credential_path, "correct-horse-battery-staple")
    store = KnowledgeStore(tmp_path / "voice.sqlite3")
    store.migrate()
    store.replace_source("weekly.txt", "digest", (("周报", (1.0, 0.0)),))
    service = FakeKnowledgeIndexService(changed=2)
    app = create_app(
        AuthService(credential_path, PUBLIC_ORIGIN),
        knowledge_store=store,
        knowledge_index_service=service,
    )

    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        anonymous = client.post("/api/knowledge/reindex", headers={"Origin": PUBLIC_ORIGIN})
        login = client.post(
            "/api/auth/login",
            headers={"Origin": PUBLIC_ORIGIN},
            json={"password": "correct-horse-battery-staple"},
        )
        missing_csrf = client.post("/api/knowledge/reindex", headers={"Origin": PUBLIC_ORIGIN})
        rebuilt = client.post(
            "/api/knowledge/reindex",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]},
        )

    assert anonymous.status_code == 401
    assert missing_csrf.status_code == 403
    assert rebuilt.status_code == 200
    assert rebuilt.json()["changed_source_count"] == 2
    assert rebuilt.json()["source_count"] == 1
    assert "weekly" not in rebuilt.text
    assert service.calls == 1


def test_knowledge_sources_require_login_and_return_metadata_only(tmp_path: Path) -> None:
    credential_path = tmp_path / "admin.json"
    provision_credentials(credential_path, "correct-horse-battery-staple")
    store = KnowledgeStore(tmp_path / "voice.sqlite3")
    store.migrate()
    store.replace_source("notes/weekly.md", "digest", (("private source body", (1.0, 0.0)),))
    app = create_app(AuthService(credential_path, PUBLIC_ORIGIN), knowledge_store=store)
    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        assert client.get("/api/knowledge/sources").status_code == 401
        client.post(
            "/api/auth/login",
            headers={"Origin": PUBLIC_ORIGIN},
            json={"password": "correct-horse-battery-staple"},
        )
        response = client.get("/api/knowledge/sources?query=weekly")
    assert response.status_code == 200
    assert response.json()[0]["path"] == "notes/weekly.md"
    assert "private source body" not in response.text
