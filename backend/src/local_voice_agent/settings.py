"""Configuration and writable-path confinement for the local application."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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

    def ensure_runtime_directories(self) -> None:
        """Create the bounded runtime directories required by the application."""
        for relative_path in ("cache", "db", "knowledge/inbox", "logs", "models", "voices"):
            (self.data_dir / relative_path).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide settings instance after validating writable paths."""
    return Settings()
