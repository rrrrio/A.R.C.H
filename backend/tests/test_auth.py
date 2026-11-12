"""Tests for local administrator credentials, sessions, CSRF, and origin checks."""

from pathlib import Path

from fastapi.testclient import TestClient

from arch.auth.credentials import (
    load_credentials,
    provision_credentials,
    verify_password,
)
from arch.auth.service import SESSION_COOKIE_NAME, AuthService
from arch.main import create_app

PUBLIC_ORIGIN = "https://voice.test:8443"


def test_provisioned_credentials_are_argon2id_and_never_store_the_password(tmp_path: Path) -> None:
    """Provisioning stores only a valid hash in a mode-restricted local file."""
    path = tmp_path / "admin.json"

    provision_credentials(path, "correct-horse-battery-staple")

    record = load_credentials(path)
    assert record is not None
    assert record.password_hash.startswith("$argon2id$")
    assert "correct-horse-battery-staple" not in path.read_text(encoding="utf-8")
    assert verify_password(record, "correct-horse-battery-staple")
    assert not verify_password(record, "incorrect-password")


def test_login_requires_provisioning_and_trusted_origin(tmp_path: Path) -> None:
    """An unprovisioned service rejects login before any session is issued."""
    service = AuthService(tmp_path / "admin.json", PUBLIC_ORIGIN)
    with TestClient(create_app(service), base_url=PUBLIC_ORIGIN) as client:
        missing = client.post(
            "/api/auth/login",
            headers={"Origin": PUBLIC_ORIGIN},
            json={"password": "correct-horse-battery-staple"},
        )
        wrong_origin = client.post(
            "/api/auth/login",
            headers={"Origin": "https://attacker.example"},
            json={"password": "correct-horse-battery-staple"},
        )

    assert missing.status_code == 503
    assert wrong_origin.status_code == 403


def test_login_logout_and_csrf_protection(tmp_path: Path) -> None:
    """A trusted login creates a Secure cookie and logout revokes it with CSRF proof."""
    credential_path = tmp_path / "admin.json"
    provision_credentials(credential_path, "correct-horse-battery-staple")
    service = AuthService(credential_path, PUBLIC_ORIGIN)
    headers = {"Origin": PUBLIC_ORIGIN}
    with TestClient(create_app(service), base_url=PUBLIC_ORIGIN) as client:
        rejected = client.post(
            "/api/auth/login", headers=headers, json={"password": "incorrect-password"}
        )
        accepted = client.post(
            "/api/auth/login", headers=headers, json={"password": "correct-horse-battery-staple"}
        )
        csrf_token = accepted.json()["csrf_token"]
        logged_in_status = client.get("/api/auth/status")
        refreshed_csrf = client.get("/api/auth/csrf")
        cookie = accepted.headers["set-cookie"]
        no_csrf = client.post("/api/auth/logout", headers=headers)
        logged_out = client.post(
            "/api/auth/logout", headers={**headers, "X-CSRF-Token": csrf_token}
        )
        status_after_logout = client.get("/api/auth/status")

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert logged_in_status.json()["authenticated"] is True
    assert logged_in_status.json()["terminal_lease_scopes"] == [
        "registered_observe",
        "home_metadata",
    ]
    assert logged_in_status.json()["terminal_lease_expires_at"]
    assert refreshed_csrf.json() == {"csrf_token": csrf_token}
    assert SESSION_COOKIE_NAME in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    assert no_csrf.status_code == 403
    assert logged_out.status_code == 204
    assert status_after_logout.json() == {
        "configured": True,
        "authenticated": False,
        "terminal_lease_expires_at": None,
        "terminal_lease_scopes": [],
        "terminal_profile": None,
    }


def test_terminal_lease_can_be_reissued_with_password_and_csrf(tmp_path: Path) -> None:
    """An authenticated browser can rotate an expired read-only lease without logging out."""
    credential_path = tmp_path / "admin.json"
    provision_credentials(credential_path, "correct-horse-battery-staple")
    service = AuthService(credential_path, PUBLIC_ORIGIN)
    headers = {"Origin": PUBLIC_ORIGIN}
    with TestClient(create_app(service), base_url=PUBLIC_ORIGIN) as client:
        login = client.post(
            "/api/auth/login", headers=headers, json={"password": "correct-horse-battery-staple"}
        )
        csrf_token = login.json()["csrf_token"]
        renewed = client.post(
            "/api/auth/terminal-lease/reissue",
            headers={**headers, "X-CSRF-Token": csrf_token},
            json={"password": "correct-horse-battery-staple"},
        )
        no_csrf = client.post(
            "/api/auth/terminal-lease/reissue",
            headers=headers,
            json={"password": "correct-horse-battery-staple"},
        )
        wrong_password = client.post(
            "/api/auth/terminal-lease/reissue",
            headers={**headers, "X-CSRF-Token": csrf_token},
            json={"password": "wrong"},
        )

    assert renewed.status_code == 200
    assert renewed.json()["terminal_lease_expires_at"]
    assert renewed.json()["terminal_lease_scopes"] == ["registered_observe", "home_metadata"]
    assert no_csrf.status_code == 403
    assert wrong_password.status_code == 401
