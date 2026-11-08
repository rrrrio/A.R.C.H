"""Tests for authenticated, session-owned SmallWebRTC signaling."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
)

from local_voice_agent.auth.credentials import provision_credentials
from local_voice_agent.auth.service import AuthService
from local_voice_agent.main import create_app
from local_voice_agent.voice.webrtc import WebRTCSignalingService

PUBLIC_ORIGIN = "https://voice.test:8443"


class FakeSmallWebRTCHandler:
    """Record signaling calls without constructing an actual peer connection in unit tests."""

    def __init__(self) -> None:
        self.offers: list[SmallWebRTCRequest] = []
        self.patches: list[SmallWebRTCPatchRequest] = []
        self.close_count = 0

    async def handle_web_request(
        self,
        request: SmallWebRTCRequest,
        _: Callable[[Any], Awaitable[None]],
    ) -> dict[str, str]:
        self.offers.append(request)
        return {"sdp": "answer-sdp", "type": "answer", "pc_id": "pc-1"}

    async def handle_patch_request(self, request: SmallWebRTCPatchRequest) -> None:
        self.patches.append(request)

    async def close(self) -> None:
        self.close_count += 1


def make_authenticated_client(tmp_path: Any) -> tuple[TestClient, FakeSmallWebRTCHandler]:
    """Create a configured test browser and its inspectable signaling fake."""
    credential_path = tmp_path / "admin.json"
    provision_credentials(credential_path, "correct-horse-battery-staple")
    handler = FakeSmallWebRTCHandler()
    app = create_app(
        AuthService(credential_path, PUBLIC_ORIGIN),
        WebRTCSignalingService(handler),
    )
    return TestClient(app, base_url=PUBLIC_ORIGIN), handler


def test_voice_offer_and_candidates_require_authenticated_csrf_session(tmp_path: Any) -> None:
    """Only a logged-in same-origin browser can create and extend a voice connection."""
    client, handler = make_authenticated_client(tmp_path)
    headers = {"Origin": PUBLIC_ORIGIN}
    with client:
        anonymous = client.post(
            "/api/voice/offer",
            headers=headers,
            json={"sdp": "offer", "type": "offer"},
        )
        login = client.post(
            "/api/auth/login", headers=headers, json={"password": "correct-horse-battery-staple"}
        )
        csrf_token = login.json()["csrf_token"]
        missing_csrf = client.post(
            "/api/voice/offer", headers=headers, json={"sdp": "offer", "type": "offer"}
        )
        answer = client.post(
            "/api/voice/offer",
            headers={**headers, "X-CSRF-Token": csrf_token},
            json={"sdp": "offer", "type": "offer"},
        )
        candidate = client.patch(
            "/api/voice/candidates",
            headers={**headers, "X-CSRF-Token": csrf_token},
            json={
                "pc_id": "pc-1",
                "candidates": [
                    {"candidate": "candidate:1", "sdp_mid": "0", "sdp_mline_index": 0}
                ],
            },
        )
        closed = client.delete(
            "/api/voice/session", headers={**headers, "X-CSRF-Token": csrf_token}
        )

    assert anonymous.status_code == 401
    assert login.status_code == 200
    assert missing_csrf.status_code == 403
    assert answer.json() == {"sdp": "answer-sdp", "type": "answer", "pc_id": "pc-1"}
    assert candidate.status_code == 204
    assert closed.status_code == 204
    assert [offer.sdp for offer in handler.offers] == ["offer"]
    assert len(handler.patches) == 1
    assert handler.close_count >= 1


def test_second_session_cannot_take_an_existing_voice_connection() -> None:
    """The owner session is the only caller permitted to renegotiate or release its peer."""
    service = WebRTCSignalingService(FakeSmallWebRTCHandler())

    async def exercise() -> None:
        await service.offer("owner", SmallWebRTCRequest(sdp="offer", type="offer"))
        with pytest.raises(HTTPException) as error:
            await service.offer("other", SmallWebRTCRequest(sdp="offer", type="offer"))
        assert error.value.status_code == 409
        with pytest.raises(HTTPException) as error:
            await service.add_candidates(
                "other",
                "pc-1",
                [IceCandidate(candidate="candidate:1", sdp_mid="0", sdp_mline_index=0)],
            )
        assert error.value.status_code == 409

    asyncio.run(exercise())
