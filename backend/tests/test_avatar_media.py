"""Tests for backend-owned avatar media proxying."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from arch.auth.credentials import provision_credentials
from arch.auth.service import AuthService
from arch.avatar.media import (
    AvatarMediaAnswer,
    AvatarMediaService,
    LoopbackAvatarMediaRuntimeClient,
)
from arch.avatar.video import AvatarVideoSignalingService
from arch.main import create_app


class Runtime:
    def __init__(self) -> None:
        self.ticket = ""
        self.session_id = ""
        self.submitted: tuple[str, str, bytes] | None = None
        self.cancelled: tuple[str, str] | None = None

    async def create_session(
        self, *, ticket: str, session_id: str, offer_sdp: str
    ) -> AvatarMediaAnswer:
        self.ticket = ticket
        self.session_id = session_id
        assert offer_sdp == "browser-offer"
        return AvatarMediaAnswer(sdp="runtime-answer", pc_id="opaque-peer")

    async def close_session(self, *, session_id: str) -> None:
        self.session_id = session_id

    async def submit_utterance(
        self, *, session_id: str, utterance_id: str, pcm_24khz: bytes
    ) -> None:
        self.submitted = (session_id, utterance_id, pcm_24khz)

    async def cancel_utterance(self, *, session_id: str, utterance_id: str) -> None:
        self.cancelled = (session_id, utterance_id)

    async def health(self) -> bool:
        return True


@pytest.mark.parametrize(
    "url",
    ["https://127.0.0.1:8100", "http://localhost:8100", "http://127.0.0.1:8100/session"],
)
def test_loopback_runtime_client_rejects_non_loopback_control_urls(url: str) -> None:
    with pytest.raises(ValueError, match="runtime"):
        LoopbackAvatarMediaRuntimeClient(url)


def test_loopback_runtime_client_never_inherits_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tickets and PCM must bypass every user-configured HTTP proxy."""
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict[str, str]:
            return {"sdp": "answer", "type": "answer", "pc_id": "peer"}

    class Client:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *arguments: object) -> None:
            return None

        async def post(self, *arguments: object, **kwargs: object) -> Response:
            return Response()

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:6152")
    monkeypatch.setattr("arch.avatar.media.httpx.AsyncClient", Client)

    answer = asyncio.run(
        LoopbackAvatarMediaRuntimeClient("http://127.0.0.1:8101").create_session(
            ticket="ticket", session_id="session", offer_sdp="offer"
        )
    )

    assert answer == AvatarMediaAnswer(sdp="answer", pc_id="peer")
    assert captured["trust_env"] is False


def test_media_service_keeps_ticket_inside_loopback_proxy() -> None:
    runtime = Runtime()
    service = AvatarMediaService(runtime=runtime)
    answer = asyncio.run(service.offer("session", "browser-offer"))

    assert answer == AvatarMediaAnswer(sdp="runtime-answer", pc_id="opaque-peer")
    assert runtime.session_id == "session"
    assert runtime.ticket
    assert "session" not in runtime.ticket


def test_media_service_submits_and_cancels_pcm_only_through_its_private_runtime() -> None:
    runtime = Runtime()
    service = AvatarMediaService(runtime=runtime)

    asyncio.run(service.submit_utterance("session", "answer", b"\x00\x01"))
    asyncio.run(service.cancel_utterance("session", "answer"))
    asyncio.run(service.release("closed-session"))

    assert runtime.submitted == ("session", "answer", b"\x00\x01")
    assert runtime.cancelled == ("session", "answer")
    assert runtime.session_id == "closed-session"


def test_offer_api_proxies_authenticated_session_without_exposing_ticket(tmp_path) -> None:
    credentials = tmp_path / "admin.json"
    origin = "https://avatar.test:8443"
    provision_credentials(credentials, "correct-horse-battery-staple")
    runtime = Runtime()
    app = create_app(
        AuthService(credentials, origin), avatar_media_service=AvatarMediaService(runtime=runtime)
    )

    with TestClient(app, base_url=origin) as client:
        assert client.post("/api/avatar/ticket").status_code == 404
        login = client.post(
            "/api/auth/login",
            headers={"Origin": origin},
            json={"password": "correct-horse-battery-staple"},
        )
        csrf = login.json()["csrf_token"]
        response = client.post(
            "/api/avatar/offer",
            headers={"Origin": origin, "X-CSRF-Token": csrf},
            json={"sdp": "browser-offer", "type": "offer"},
        )
        timing = client.post(
            "/api/avatar/timing",
            headers={"Origin": origin, "X-CSRF-Token": csrf},
            json={"stage": "browser-first-video-decoded", "elapsed_ms": 42},
        )

    assert response.status_code == 200
    assert response.json() == {"sdp": "runtime-answer", "type": "answer", "pc_id": "opaque-peer"}
    assert runtime.ticket
    assert "ticket" not in response.text
    assert timing.status_code == 204


def test_avatar_timing_rejects_payloads_without_an_active_media_peer(tmp_path) -> None:
    credentials = tmp_path / "admin.json"
    origin = "https://avatar.test:8443"
    provision_credentials(credentials, "correct-horse-battery-staple")
    app = create_app(AuthService(credentials, origin), avatar_media_service=AvatarMediaService())

    with TestClient(app, base_url=origin) as client:
        login = client.post(
            "/api/auth/login",
            headers={"Origin": origin},
            json={"password": "correct-horse-battery-staple"},
        )
        response = client.post(
            "/api/avatar/timing",
            headers={"Origin": origin, "X-CSRF-Token": login.json()["csrf_token"]},
            json={"stage": "browser-first-video-decoded", "elapsed_ms": 42},
        )

    assert response.status_code == 409


def test_status_reports_unavailable_when_the_configured_runtime_fails_health_check(
    tmp_path,
) -> None:
    """The browser must not attempt WebRTC against a configured but stopped loopback service."""
    class StoppedRuntime(Runtime):
        async def health(self) -> bool:
            return False

    credentials = tmp_path / "admin.json"
    origin = "https://avatar.test:8443"
    provision_credentials(credentials, "correct-horse-battery-staple")
    media = AvatarMediaService(runtime=StoppedRuntime())
    avatar = AvatarVideoSignalingService(
        enabled=True,
        model="wav2lip",
        source_video=tmp_path / "idle.mp4",
        socket_path=tmp_path / "legacy.sock",
        runtime_configured=media.is_configured,
    )
    app = create_app(
        AuthService(credentials, origin),
        avatar_video_service=avatar,
        avatar_media_service=media,
    )

    with TestClient(app, base_url=origin) as client:
        login = client.post(
            "/api/auth/login",
            headers={"Origin": origin},
            json={"password": "correct-horse-battery-staple"},
        )
        response = client.get("/api/avatar/status")

    assert login.status_code == 200
    assert response.json() == {
        "enabled": False,
        "state": "unavailable",
        "model": "wav2lip",
        "source_video": "idle.mp4",
        "detail": "Avatar media runtime is unavailable.",
    }
