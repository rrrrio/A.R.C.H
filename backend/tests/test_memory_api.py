"""Tests for authenticated browser review of candidate memories."""

from pathlib import Path

from fastapi.testclient import TestClient

from arch.auth.credentials import provision_credentials
from arch.auth.service import AuthService
from arch.main import create_app
from arch.memory.context import MAX_MEMORY_CANDIDATE_CHARS
from arch.memory.store import MemoryStore

PUBLIC_ORIGIN = "https://voice.test:8443"


def test_memory_candidates_require_login_and_csrf_for_edits(tmp_path: Path) -> None:
    """Only the local authenticated administrator can edit or approve a pending candidate."""
    credential_path = tmp_path / "admin.json"
    provision_credentials(credential_path, "correct-horse-battery-staple")
    store = MemoryStore(tmp_path / "voice.sqlite3")
    store.migrate()
    conversation_id = store.create_conversation()
    message = store.append_message(conversation_id, "turn-1", "user", "我喜欢简洁回答")
    candidate = store.create_candidate(message.id, "用户偏好简洁回答")
    app = create_app(AuthService(credential_path, PUBLIC_ORIGIN), memory_store=store)

    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        anonymous = client.get("/api/memory/candidates")
        login = client.post(
            "/api/auth/login",
            headers={"Origin": PUBLIC_ORIGIN},
            json={"password": "correct-horse-battery-staple"},
        )
        csrf_token = login.json()["csrf_token"]
        listed = client.get("/api/memory/candidates")
        missing_csrf = client.patch(
            f"/api/memory/candidates/{candidate.id}",
            headers={"Origin": PUBLIC_ORIGIN},
            json={"content": "用户偏好短回答"},
        )
        oversized = client.patch(
            f"/api/memory/candidates/{candidate.id}",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf_token},
            json={"content": "x" * (MAX_MEMORY_CANDIDATE_CHARS + 1)},
        )
        edited = client.patch(
            f"/api/memory/candidates/{candidate.id}",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf_token},
            json={"content": "用户偏好短回答"},
        )
        approved = client.patch(
            f"/api/memory/candidates/{candidate.id}",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf_token},
            json={"action": "approved"},
        )
        pending_after_approval = client.get("/api/memory/candidates")
        approved_listed = client.get("/api/memory/candidates?status=approved")
        deleted = client.patch(
            f"/api/memory/candidates/{candidate.id}",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf_token},
            json={"action": "deleted"},
        )
        approved_after_deletion = client.get("/api/memory/candidates?status=approved")

    assert anonymous.status_code == 401
    assert listed.json() == [
        {
            "id": candidate.id,
            "source_message_id": message.id,
            "content": "用户偏好简洁回答",
            "status": "pending",
            "created_at": candidate.created_at,
            "updated_at": candidate.updated_at,
        }
    ]
    assert missing_csrf.status_code == 403
    assert oversized.status_code == 422
    assert edited.json()["content"] == "用户偏好短回答"
    assert approved.json()["status"] == "approved"
    assert pending_after_approval.json() == []
    assert approved_listed.json()[0]["id"] == candidate.id
    assert deleted.json()["status"] == "deleted"
    assert approved_after_deletion.json() == []
