"""Apply the A.R.C.H.'s versioned SQLite migrations."""

from arch.memory.store import MemoryStore
from arch.settings import get_settings


def main() -> None:
    """Migrate the configured project-contained database and print applied versions."""
    settings = get_settings()
    settings.ensure_runtime_directories()
    applied = MemoryStore(settings.database_path).migrate()
    print(f"Applied migrations: {', '.join(str(version) for version in applied) or 'none'}")


if __name__ == "__main__":
    main()
