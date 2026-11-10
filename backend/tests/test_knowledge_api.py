"""Tests for the authenticated local knowledge-index status endpoint."""

from pathlib import Path

from fastapi.testclient import TestClient

from arch.auth.credentials import provision_credentials
from arch.auth.service import AuthService
from arch.main import create_app
from arch.rag.store import KnowledgeStore

PUBLIC_ORIGIN = "https://voice.test:8443"


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
