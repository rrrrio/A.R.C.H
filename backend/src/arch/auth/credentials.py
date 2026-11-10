"""Argon2id credential storage for the single local administrator."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

_PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


@dataclass(frozen=True)
class CredentialRecord:
    """Versioned password hash record that never contains a plaintext secret."""

    password_hash: str
    version: int = 1


def load_credentials(path: Path) -> CredentialRecord | None:
    """Return the administrator hash record, or None before initial provisioning."""
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError("Administrator credential file is unreadable.") from error
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Administrator credential file has an unsupported format.")
    password_hash = value.get("password_hash")
    if not isinstance(password_hash, str) or not password_hash:
        raise ValueError("Administrator credential file does not contain a password hash.")
    return CredentialRecord(password_hash=password_hash)


def provision_credentials(path: Path, password: str, *, replace: bool = False) -> None:
    """Hash a new password and atomically write a mode-0600 credential record."""
    if len(password) < 12:
        raise ValueError("Administrator password must contain at least 12 characters.")
    if path.exists() and not replace:
        raise FileExistsError(
            "Administrator credentials already exist; pass --replace to rotate them."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    record = CredentialRecord(password_hash=_PASSWORD_HASHER.hash(password))
    with tempfile.NamedTemporaryFile(
        dir=path.parent, mode="w", encoding="utf-8", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        temporary.write(json.dumps(record.__dict__, separators=(",", ":")) + "\n")
    os.chmod(temporary_path, 0o600)
    temporary_path.replace(path)


def verify_password(record: CredentialRecord, password: str) -> bool:
    """Return whether a plaintext password verifies against the stored Argon2id hash."""
    try:
        return _PASSWORD_HASHER.verify(record.password_hash, password)
    except (InvalidHashError, VerificationError):
        return False
