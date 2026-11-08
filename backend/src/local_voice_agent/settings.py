"""Configuration and writable-path confinement for the local application."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Application settings loaded from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="LVA_",
        extra="ignore",
    )

    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    data_dir: Path = PROJECT_ROOT / "data"
    public_origin: str = "https://local-voice-agent.local:8443"
    search_enabled: bool = False
    searxng_endpoint: str = "http://127.0.0.1:8080"

    @field_validator("data_dir")
    @classmethod
    def data_dir_must_be_under_project(cls, value: Path) -> Path:
        """Reject a writable data root outside the repository's data directory."""
        candidate = value if value.is_absolute() else PROJECT_ROOT / value
        resolved = candidate.resolve()
        allowed_root = (PROJECT_ROOT / "data").resolve()
        if resolved != allowed_root and allowed_root not in resolved.parents:
            message = f"LVA_DATA_DIR must be inside {allowed_root}, got {resolved}"
            raise ValueError(message)
        return resolved

    @field_validator("public_origin")
    @classmethod
    def public_origin_must_be_https_origin(cls, value: str) -> str:
        """Require the same HTTPS origin that browsers use for state-changing requests."""
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("LVA_PUBLIC_ORIGIN must be a bare HTTPS origin.")
        return value.rstrip("/")

    @field_validator("searxng_endpoint")
    @classmethod
    def searxng_endpoint_must_be_loopback_origin(cls, value: str) -> str:
        """Keep the application's search client behind its local SearXNG boundary."""
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("LVA_SEARXNG_ENDPOINT must be a bare HTTP loopback origin.")
        return value.rstrip("/")

    def ensure_runtime_directories(self) -> None:
        """Create the bounded runtime directories required by the application."""
        for relative_path in (
            "auth",
            "cache",
            "db",
            "knowledge/inbox",
            "logs",
            "models",
            "workspace",
            "voices",
        ):
            (self.data_dir / relative_path).mkdir(parents=True, exist_ok=True)

    @property
    def credential_path(self) -> Path:
        """Return the bounded, local-only administrator credential location."""
        return self.data_dir / "auth" / "admin.json"

    @property
    def database_path(self) -> Path:
        """Return the bounded SQLite database path for persistent local state."""
        return self.data_dir / "db" / "local-voice-agent.sqlite3"

    @property
    def rag_model_path(self) -> Path:
        """Return the fixed local BGE-M3 snapshot used for offline knowledge retrieval."""
        return self.data_dir / "models/bge-m3/5617a9f61b028005a4858fdac845db406aefb181"

    @property
    def knowledge_inbox_path(self) -> Path:
        """Return the only user-managed source directory eligible for RAG indexing."""
        return self.data_dir / "knowledge/inbox"

    @property
    def terminal_workspace_path(self) -> Path:
        """Return the only writable root exposed to the first terminal tool release."""
        return self.data_dir / "workspace"


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide settings instance after validating writable paths."""
    return Settings()
