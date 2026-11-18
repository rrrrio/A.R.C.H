"""Authenticated browser contracts for conversation profile settings."""

from pathlib import Path

from fastapi.testclient import TestClient

from arch.auth.credentials import provision_credentials
from arch.auth.service import AuthService
from arch.conversation.profiles import ConversationProfileStore
from arch.main import create_app

PUBLIC_ORIGIN = "https://arch.test"


def test_conversation_profiles_require_authentication_and_csrf_then_reset_selection(
    tmp_path: Path,
) -> None:
    """The browser may manage its own profiles but cannot write them anonymously."""
    credential_path = tmp_path / "admin.json"
    provision_credentials(credential_path, "correct-horse-battery-staple")
    store = ConversationProfileStore(tmp_path / "voice.sqlite3")
    store.migrate()
    app = create_app(
        AuthService(credential_path, PUBLIC_ORIGIN),
        conversation_profile_store=store,
    )

    payload = {
        "name": "小雅",
        "persona": "温和回应用户，并自然追问相关细节。",
        "speaking_style": "口语化，每次一到三句。",
        "examples": [{"user": "今天好累", "assistant": "辛苦了呀，今天最累的是哪一段？"}],
        "social_temperature": 0.7,
    }
    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        assert client.get("/api/conversation/profiles").status_code == 401
        login = client.post(
            "/api/auth/login",
            headers={"Origin": PUBLIC_ORIGIN},
            json={"password": "correct-horse-battery-staple"},
        )
        csrf = login.json()["csrf_token"]
        assert client.post("/api/conversation/profiles", json=payload).status_code == 403
        created = client.post(
            "/api/conversation/profiles",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf},
            json=payload,
        )
        assert created.status_code == 201
        profile_id = created.json()["id"]
        selected = client.put(
            "/api/conversation/selection",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf},
            json={"profile_id": profile_id},
        )
        assert selected.json()["selected_id"] == profile_id
        deleted = client.delete(
            f"/api/conversation/profiles/{profile_id}",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf},
        )
        listed = client.get("/api/conversation/profiles")

    assert deleted.status_code == 204
    assert listed.json() == {"selected_id": None, "profiles": []}
