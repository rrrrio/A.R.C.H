"""Authorization regressions for the loopback runtime control API."""

from __future__ import annotations

from base64 import b64encode
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from avatar_media_runtime.runtime import Answer, AvatarMediaRuntime, MediaSessionNotFoundError
from avatar_media_runtime.server import create_runtime_app
from avatar_media_runtime.tickets import MediaTicketSigner


class Runtime:
    def __init__(self) -> None:
        self.submitted: tuple[str, str, bytes] | None = None
        self.cancelled: tuple[str, str] | None = None

    async def create_session(self, ticket, offer_sdp: str) -> Answer:
        assert ticket.session_id == "browser-session"
        assert offer_sdp == "offer"
        return Answer(session_id=ticket.session_id, sdp="answer")

    async def close_session(self, session_id: str) -> None:
        assert session_id

    async def submit(self, session_id: str, utterance_id: str, pcm_24khz: bytes) -> None:
        self.submitted = (session_id, utterance_id, pcm_24khz)

    async def cancel(self, session_id: str, utterance_id: str) -> None:
        self.cancelled = (session_id, utterance_id)

    def is_ready(self) -> bool:
        return True


def test_runtime_rejects_direct_browser_requests_and_reused_tickets() -> None:
    signer = MediaTicketSigner(b"a" * 32)
    app = create_runtime_app(Runtime(), signer)  # type: ignore[arg-type]
    expires_at = datetime.now(UTC) + timedelta(minutes=1)
    ticket = signer.issue("browser-session", expires_at)

    with TestClient(app) as client:
        assert client.post("/session", json={"sdp": "offer"}).status_code == 403
        first = client.post(
            "/session",
            headers={
                "X-Avatar-Media-Ticket": ticket,
                "X-Avatar-Session-Id": "other-session",
            },
            json={"sdp": "offer"},
        )
        second = client.post(
            "/session",
            headers={
                "X-Avatar-Media-Ticket": ticket,
                "X-Avatar-Session-Id": "browser-session",
            },
            json={"sdp": "offer"},
        )
        third = client.post(
            "/session",
            headers={
                "X-Avatar-Media-Ticket": ticket,
                "X-Avatar-Session-Id": "browser-session",
            },
            json={"sdp": "offer"},
        )

    assert first.status_code == 403
    assert second.status_code == 200
    assert set(second.json()) == {"sdp", "type", "pc_id"}
    assert third.status_code == 403


def test_runtime_health_is_available_without_a_media_ticket() -> None:
    app = create_runtime_app(Runtime(), MediaTicketSigner(b"a" * 32))  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.json() == {"status": "ready"}


def test_runtime_health_rejects_a_process_without_a_media_backend() -> None:
    app = create_runtime_app(AvatarMediaRuntime(), MediaTicketSigner(b"a" * 32))

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 503


def test_runtime_submits_and_cancels_complete_pcm_only_for_a_session() -> None:
    runtime = Runtime()
    app = create_runtime_app(runtime, MediaTicketSigner(b"a" * 32))  # type: ignore[arg-type]
    payload = {"utterance_id": "answer", "pcm_24khz_base64": b64encode(b"\x00\x01").decode()}

    with TestClient(app) as client:
        assert client.post("/utterance", json=payload).status_code == 403
        submitted = client.post(
            "/utterance", headers={"X-Avatar-Session-Id": "session"}, json=payload
        )
        cancelled = client.post(
            "/utterance/cancel",
            headers={"X-Avatar-Session-Id": "session"},
            json={"utterance_id": "answer"},
        )

    assert submitted.status_code == 204
    assert cancelled.status_code == 204
    assert runtime.submitted == ("session", "answer", b"\x00\x01")
    assert runtime.cancelled == ("session", "answer")


def test_runtime_reports_a_closed_session_submission_as_recoverable_unavailable() -> None:
    class ClosedSessionRuntime(Runtime):
        async def submit(self, session_id: str, utterance_id: str, pcm_24khz: bytes) -> None:
            del session_id, utterance_id, pcm_24khz
            raise MediaSessionNotFoundError("Avatar media session does not exist.")

    app = create_runtime_app(ClosedSessionRuntime(), MediaTicketSigner(b"a" * 32))  # type: ignore[arg-type]
    payload = {"utterance_id": "answer", "pcm_24khz_base64": b64encode(b"\x00\x01").decode()}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/utterance", headers={"X-Avatar-Session-Id": "session"}, json=payload
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Avatar media unavailable."}


def test_runtime_rejects_a_second_valid_ticket_for_the_same_peer() -> None:
    class ConflictRuntime(Runtime):
        def __init__(self) -> None:
            super().__init__()
            self.created = False

        async def create_session(self, ticket, offer_sdp: str) -> Answer:
            if self.created:
                raise RuntimeError("Avatar media session already exists.")
            self.created = True
            return await super().create_session(ticket, offer_sdp)

    signer = MediaTicketSigner(b"a" * 32)
    app = create_runtime_app(ConflictRuntime(), signer)  # type: ignore[arg-type]
    expires_at = datetime.now(UTC) + timedelta(minutes=1)
    first_ticket = signer.issue("browser-session", expires_at)
    second_ticket = signer.issue("browser-session", expires_at)

    with TestClient(app) as client:
        headers = {"X-Avatar-Session-Id": "browser-session"}
        assert client.post(
            "/session",
            headers={**headers, "X-Avatar-Media-Ticket": first_ticket},
            json={"sdp": "offer"},
        ).status_code == 200
        assert client.post(
            "/session",
            headers={**headers, "X-Avatar-Media-Ticket": second_ticket},
            json={"sdp": "offer"},
        ).status_code == 409
