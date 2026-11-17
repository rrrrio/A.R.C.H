"""Tests for one-use, session-bound runtime media tickets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from avatar_media_runtime.tickets import MediaTicketSigner, TicketError


def test_ticket_is_one_use_and_bound_to_its_browser_session() -> None:
    signer = MediaTicketSigner(b"test-secret-value")
    token = signer.issue("browser-session", datetime.now(UTC) + timedelta(minutes=1))

    ticket = signer.verify_and_consume(token, "browser-session")

    assert ticket.session_id == "browser-session"
    with pytest.raises(TicketError, match="already used"):
        signer.verify_and_consume(token, "browser-session")


def test_ticket_rejects_another_session_or_an_expired_deadline() -> None:
    signer = MediaTicketSigner(b"test-secret-value")
    valid = signer.issue("browser-session", datetime.now(UTC) + timedelta(minutes=1))
    expired = signer.issue("browser-session", datetime.now(UTC) - timedelta(seconds=1))

    with pytest.raises(TicketError, match="session"):
        signer.verify_and_consume(valid, "other-session")
    with pytest.raises(TicketError, match="expired"):
        signer.verify_and_consume(expired, "browser-session")
