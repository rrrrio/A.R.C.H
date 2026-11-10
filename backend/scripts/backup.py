"""Create a local A.R.C.H. data backup archive."""

from datetime import UTC, datetime

from arch.backup import create_backup
from arch.settings import get_settings


def main() -> None:
    settings = get_settings()
    destination = (
        settings.data_dir
        / "backups"
        / f"arch-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.tar.gz"
    )
    print(create_backup(settings.data_dir, destination))


if __name__ == "__main__":
    main()
