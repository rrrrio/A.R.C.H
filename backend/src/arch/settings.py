"""Configuration and writable-path confinement for A.R.C.H."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Application settings loaded from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="",
        populate_by_name=True,
        extra="ignore",
    )

    environment: str = Field(
        default="development",
        validation_alias=AliasChoices("ARCH_ENVIRONMENT", "LVA_ENVIRONMENT"),
    )
    host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("ARCH_HOST", "LVA_HOST"),
    )
    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("ARCH_PORT", "LVA_PORT"),
    )
    data_dir: Path = Field(
        default=PROJECT_ROOT / "data",
        validation_alias=AliasChoices("ARCH_DATA_DIR", "LVA_DATA_DIR"),
    )
    artifact_dir: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("ARCH_ARTIFACT_DIR", "LVA_ARTIFACT_DIR"),
    )
    public_origin: str = Field(
        default="https://arch.local:8443",
        validation_alias=AliasChoices("ARCH_PUBLIC_ORIGIN", "LVA_PUBLIC_ORIGIN"),
    )
    search_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("ARCH_SEARCH_ENABLED", "LVA_SEARCH_ENABLED"),
    )
    searxng_endpoint: str = Field(
        default="http://127.0.0.1:8080",
        validation_alias=AliasChoices("ARCH_SEARXNG_ENDPOINT", "LVA_SEARXNG_ENDPOINT"),
    )
    video_avatar_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("ARCH_VIDEO_AVATAR_ENABLED", "LVA_VIDEO_AVATAR_ENABLED"),
    )
    avatar_media_runtime_url: str = Field(
        default="http://127.0.0.1:8101",
        validation_alias=AliasChoices(
            "ARCH_AVATAR_MEDIA_RUNTIME_URL", "LVA_AVATAR_MEDIA_RUNTIME_URL"
        ),
    )
    avatar_media_ticket_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ARCH_AVATAR_MEDIA_TICKET_SECRET", "LVA_AVATAR_MEDIA_TICKET_SECRET"
        ),
    )

    @field_validator("data_dir")
    @classmethod
    def data_dir_must_be_under_project(cls, value: Path) -> Path:
        """Reject a writable data root outside the repository's data directory."""
        candidate = value if value.is_absolute() else PROJECT_ROOT / value
        resolved = candidate.resolve()
        allowed_root = (PROJECT_ROOT / "data").resolve()
        if resolved != allowed_root and allowed_root not in resolved.parents:
            message = f"ARCH_DATA_DIR must be inside {allowed_root}, got {resolved}"
            raise ValueError(message)
        return resolved

    @field_validator("artifact_dir")
    @classmethod
    def artifact_dir_must_be_under_project(cls, value: Path | None) -> Path | None:
        """Allow a separate artifact source only below the project data root."""
        if value is None:
            return None
        candidate = value if value.is_absolute() else PROJECT_ROOT / value
        resolved = candidate.resolve()
        allowed_root = (PROJECT_ROOT / "data").resolve()
        if resolved != allowed_root and allowed_root not in resolved.parents:
            message = f"ARCH_ARTIFACT_DIR must be inside {allowed_root}, got {resolved}"
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
            raise ValueError("ARCH_PUBLIC_ORIGIN must be a bare HTTPS origin.")
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
            raise ValueError("ARCH_SEARXNG_ENDPOINT must be a bare HTTP loopback origin.")
        return value.rstrip("/")

    @field_validator("avatar_media_runtime_url")
    @classmethod
    def avatar_media_runtime_must_be_loopback_origin(cls, value: str) -> str:
        """Prevent the backend from sending session tickets beyond this host."""
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("ARCH_AVATAR_MEDIA_RUNTIME_URL must be a bare HTTP 127.0.0.1 origin.")
        return value.rstrip("/")

    @field_validator("avatar_media_ticket_secret")
    @classmethod
    def avatar_media_ticket_secret_must_be_strong(cls, value: SecretStr | None) -> SecretStr | None:
        """Reject a weak shared secret before it can authorize loopback media peers."""
        if value is not None and len(value.get_secret_value().encode("utf-8")) < 16:
            raise ValueError("ARCH_AVATAR_MEDIA_TICKET_SECRET must contain at least 16 bytes.")
        return value

    def ensure_runtime_directories(self) -> None:
        """Create the bounded runtime directories required by the application."""
        for relative_path in (
            "auth",
            "cache",
            "db",
            "knowledge/inbox",
            "logs",
            "models",
            "avatars",
            "workspace",
            "voices",
        ):
            (self.data_dir / relative_path).mkdir(parents=True, exist_ok=True)

    @property
    def credential_path(self) -> Path:
        """Return the bounded, local-only administrator credential location."""
        return self.data_dir / "auth" / "admin.json"

    @property
    def artifact_data_dir(self) -> Path:
        """Return the bounded source for model artifacts without widening writable state."""
        return self.artifact_dir or self.data_dir

    @property
    def database_path(self) -> Path:
        """Return the bounded SQLite database path for persistent local state."""
        database_path = self.data_dir / "db" / "arch.sqlite3"
        legacy_path = self.data_dir / "db" / "local-voice-agent.sqlite3"
        if not database_path.exists() and legacy_path.exists():
            return legacy_path
        return database_path

    @property
    def rag_model_path(self) -> Path:
        """Return the fixed local BGE-M3 snapshot used for offline knowledge retrieval."""
        return self.artifact_data_dir / "models/bge-m3/5617a9f61b028005a4858fdac845db406aefb181"

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
