"""One-use, session-bound tickets for loopback avatar runtime signaling."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone


class TicketError(ValueError):
    """Raised when a media ticket cannot authorize a runtime session."""


@dataclass(frozen=True, slots=True)
class MediaTicket:
    """Verified ticket claims that bind a request to one browser session."""

    session_id: str
    nonce: str
    expires_at: datetime


class MediaTicketSigner:
    """Issue and consume short-lived signed tickets without persistent storage."""

    def __init__(self, secret: bytes) -> None:
        if len(secret) < 16:
            raise ValueError("Media ticket secret must contain at least 16 bytes.")
        self._secret = secret
        self._consumed_nonces: set[str] = set()

    def issue(self, session_id: str, expires_at: datetime) -> str:
        """Return an opaque ticket that expires at the supplied UTC deadline."""
        if not session_id:
            raise ValueError("Media ticket session ID is required.")
        if expires_at.tzinfo is None:
            raise ValueError("Media ticket expiry must include a timezone.")
        payload = {
            "expires_at": expires_at.astimezone(timezone.utc).timestamp(),
            "nonce": secrets.token_urlsafe(24),
            "session_id": session_id,
        }
        encoded = _encode(payload)
        signature = hmac.new(self._secret, encoded, hashlib.sha256).digest()
        return f"{encoded.decode('ascii')}.{_encode_bytes(signature).decode('ascii')}"

    def verify_and_consume(self, token: str, session_id: str) -> MediaTicket:
        """Validate claims and mark the nonce consumed only after all checks pass."""
        try:
            payload_text, supplied_signature = token.split(".", maxsplit=1)
            payload_bytes = payload_text.encode("ascii")
            expected = hmac.new(self._secret, payload_bytes, hashlib.sha256).digest()
            if not hmac.compare_digest(_decode_bytes(supplied_signature), expected):
                raise TicketError("Media ticket signature is invalid.")
            payload = json.loads(_decode_bytes(payload_text))
            ticket = MediaTicket(
                session_id=str(payload["session_id"]),
                nonce=str(payload["nonce"]),
                expires_at=datetime.fromtimestamp(float(payload["expires_at"]), timezone.utc),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            if isinstance(error, TicketError):
                raise
            raise TicketError("Media ticket is malformed.") from error
        if ticket.session_id != session_id:
            raise TicketError("Media ticket belongs to another session.")
        if ticket.expires_at <= datetime.now(timezone.utc):
            raise TicketError("Media ticket has expired.")
        if ticket.nonce in self._consumed_nonces:
            raise TicketError("Media ticket has already used.")
        self._consumed_nonces.add(ticket.nonce)
        return ticket


def _encode(payload: dict[str, object]) -> bytes:
    return _encode_bytes(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _encode_bytes(value: bytes) -> bytes:
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def _decode_bytes(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
