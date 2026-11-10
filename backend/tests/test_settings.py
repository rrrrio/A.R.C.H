"""Tests for local writable-path boundaries."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from arch.settings import PROJECT_ROOT, Settings


def test_settings_allow_a_data_subdirectory() -> None:
    """A cache directory below data is valid for application-managed state."""
    settings = Settings(data_dir=Path("data/cache"))

    assert settings.data_dir == (PROJECT_ROOT / "data/cache").resolve()
    assert settings.terminal_workspace_path == (PROJECT_ROOT / "data/cache/workspace").resolve()


def test_settings_reject_paths_outside_data() -> None:
    """Configuration cannot redirect writable state into an arbitrary path."""
    with pytest.raises(ValidationError, match="ARCH_DATA_DIR must be inside"):
        Settings(data_dir=Path("/tmp/arch-escape"))


def test_settings_allow_a_separate_project_artifact_source() -> None:
    """Model inputs can be separated from writes without sourcing arbitrary host paths."""
    assert Settings(data_dir=Path("data/cache")).artifact_data_dir == (
        PROJECT_ROOT / "data/cache"
    ).resolve()
    assert Settings(artifact_dir=Path("data")).artifact_data_dir == (
        PROJECT_ROOT / "data"
    ).resolve()
    with pytest.raises(ValidationError, match="ARCH_ARTIFACT_DIR must be inside"):
        Settings(artifact_dir=Path("/tmp/arch-artifacts"))


def test_settings_reject_non_https_or_path_public_origins() -> None:
    """Browser mutation checks require one exact HTTPS origin rather than a URL prefix."""
    with pytest.raises(ValidationError, match="ARCH_PUBLIC_ORIGIN must be a bare HTTPS origin"):
        Settings(public_origin="http://voice.test:8443")
    with pytest.raises(ValidationError, match="ARCH_PUBLIC_ORIGIN must be a bare HTTPS origin"):
        Settings(public_origin="https://voice.test:8443/application")


def test_settings_default_to_disabled_loopback_search_and_reject_remote_endpoints() -> None:
    """Internet access remains opt-in and cannot route through a LAN or remote URL."""
    settings = Settings()

    assert not settings.search_enabled
    assert settings.searxng_endpoint == "http://127.0.0.1:8080"
    with pytest.raises(ValidationError, match="ARCH_SEARXNG_ENDPOINT must be a bare HTTP loopback"):
        Settings(searxng_endpoint="http://192.168.10.20:8080")


def test_settings_prefer_arch_environment_names_and_accept_legacy_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The renamed project prefers ARCH_* while existing LVA_* files keep working."""
    monkeypatch.setenv("ARCH_ENVIRONMENT", "arch")
    monkeypatch.setenv("LVA_ENVIRONMENT", "legacy")

    assert Settings().environment == "arch"

    monkeypatch.delenv("ARCH_ENVIRONMENT")
    assert Settings().environment == "legacy"


def test_settings_reuse_a_legacy_database_until_the_new_path_exists(tmp_path: Path) -> None:
    """Existing local state remains available after the project rename."""
    legacy_database = tmp_path / "db" / "local-voice-agent.sqlite3"
    legacy_database.parent.mkdir()
    legacy_database.touch()
    settings = Settings.model_construct(data_dir=tmp_path)

    assert settings.database_path == legacy_database


def test_settings_prefer_the_new_database_over_a_legacy_database(tmp_path: Path) -> None:
    """New deployments do not switch back when both database names exist."""
    database_dir = tmp_path / "db"
    database_dir.mkdir()
    (database_dir / "local-voice-agent.sqlite3").touch()
    current_database = database_dir / "arch.sqlite3"
    current_database.touch()
    settings = Settings.model_construct(data_dir=tmp_path)

    assert settings.database_path == current_database
