"""Tests for authenticated reference-voice profile endpoints."""

from __future__ import annotations

import base64
import wave
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from arch.auth.credentials import provision_credentials
from arch.auth.service import AuthService
from arch.main import create_app
from arch.voice.profiles import VoiceProfileStore

PUBLIC_ORIGIN = "https://voice.test:8443"


def _wav() -> bytes:
    payload = BytesIO()
    with wave.open(payload, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\x00\x00" * 8000)
    return payload.getvalue()


def test_voice_profiles_require_login_csrf_and_explicit_ownership(tmp_path: Path) -> None:
    """Only the administrator can manage a consented reference without receiving its path."""
    credential_path = tmp_path / "admin.json"
    provision_credentials(credential_path, "correct-horse-battery-staple")
    store = VoiceProfileStore(tmp_path / "data")
    app = create_app(
        AuthService(credential_path, PUBLIC_ORIGIN), voice_profile_store=store
    )
    payload = {
        "name": "我的声音",
        "transcript": "这是参考文本",
        "reference_wav_base64": base64.b64encode(_wav()).decode("ascii"),
        "ownership_confirmed": True,
    }

    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        anonymous = client.get("/api/voices/profiles")
        login = client.post(
            "/api/auth/login",
            headers={"Origin": PUBLIC_ORIGIN},
            json={"password": "correct-horse-battery-staple"},
        )
        csrf_token = login.json()["csrf_token"]
        missing_csrf = client.post("/api/voices/profiles", json=payload)
        rejected_origin = client.post(
            "/api/voices/profiles",
            headers={"Origin": "https://untrusted.example", "X-CSRF-Token": csrf_token},
            json=payload,
        )
        unconsented = client.post(
            "/api/voices/profiles",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf_token},
            json={**payload, "ownership_confirmed": False},
        )
        created = client.post(
            "/api/voices/profiles",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf_token},
            json=payload,
        )
        profile_id = created.json()["id"]
        selected = client.put(
            "/api/voices/selection",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf_token},
            json={"profile_id": profile_id},
        )
        deleted = client.delete(
            f"/api/voices/profiles/{profile_id}",
            headers={"Origin": PUBLIC_ORIGIN, "X-CSRF-Token": csrf_token},
        )
        listed = client.get("/api/voices/profiles")

    assert anonymous.status_code == 401
    assert missing_csrf.status_code == 403
    assert rejected_origin.status_code == 403
    assert unconsented.status_code == 422
    assert created.status_code == 201
    assert "path" not in created.json()
    assert selected.json()["selected_id"] == profile_id
    assert deleted.status_code == 204
    assert listed.json() == {"selected_id": None, "profiles": []}
