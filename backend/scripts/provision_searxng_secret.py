"""Create and validate the local-only SearXNG runtime secret."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from arch.settings import get_settings

ENVIRONMENT_VARIABLE = "SEARXNG_SECRET"
MINIMUM_SECRET_LENGTH = 32
MAXIMUM_SECRET_FILE_BYTES = 512


def secret_path(data_dir: Path) -> Path:
    """Keep the ignored SearXNG secret below the configured local runtime data root."""
    return data_dir / "runtime" / "searxng.env"


def provision_secret(path: Path) -> bool:
    """Create one mode-restricted secret file, returning whether this invocation created it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _validate_existing_secret(path)
    except FileNotFoundError:
        return _create_secret(path)
    return False


def _create_secret(path: Path) -> bool:
    """Use exclusive creation so concurrent provisioning never overwrites a live secret."""
    content = f"{ENVIRONMENT_VARIABLE}={secrets.token_urlsafe(48)}\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _validate_existing_secret(path)
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
        destination.write(content)
    return True


def _validate_existing_secret(path: Path) -> None:
    """Validate and permission an existing regular file through its descriptor."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("SearXNG secret file must be one unlinked regular file.")
        if metadata.st_size > MAXIMUM_SECRET_FILE_BYTES:
            raise ValueError("SearXNG secret file exceeds the allowed size.")
        content = os.read(descriptor, metadata.st_size).decode("utf-8")
        _validate_secret_file(content)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _validate_secret_file(content: str) -> None:
    """Reject malformed existing data rather than silently replacing a deployed secret."""
    prefix = f"{ENVIRONMENT_VARIABLE}="
    if not content.startswith(prefix) or content.count("\n") != 1 or not content.endswith("\n"):
        raise ValueError("SearXNG secret file must contain exactly one environment assignment.")
    value = content.removeprefix(prefix).removesuffix("\n")
    normalized = value.replace("-", "").replace("_", "")
    if len(value) < MINIMUM_SECRET_LENGTH or not value.isascii() or not normalized.isalnum():
        raise ValueError("SearXNG secret file contains an invalid secret value.")


def main() -> None:
    """Provision the secret without printing its value."""
    settings = get_settings()
    path = secret_path(settings.data_dir)
    created = provision_secret(path)
    action = "created" if created else "validated"
    print(f"SearXNG runtime secret {action} at {path}")


if __name__ == "__main__":
    main()
