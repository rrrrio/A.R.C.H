"""Provision or rotate the one local administrator password without echoing it."""

from __future__ import annotations

import argparse
import getpass

from local_voice_agent.auth.credentials import provision_credentials
from local_voice_agent.settings import get_settings


def main() -> None:
    """Read a password twice, then atomically write only its Argon2id hash."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace", action="store_true", help="Rotate an existing password.")
    args = parser.parse_args()
    password = getpass.getpass("Administrator password: ")
    confirmation = getpass.getpass("Confirm administrator password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    settings = get_settings()
    settings.ensure_runtime_directories()
    try:
        provision_credentials(settings.credential_path, password, replace=args.replace)
    except (FileExistsError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(f"Administrator credentials written to {settings.credential_path}")


if __name__ == "__main__":
    main()
