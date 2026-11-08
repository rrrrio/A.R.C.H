"""Tests for local writable-path boundaries."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from local_voice_agent.settings import PROJECT_ROOT, Settings


def test_settings_allow_a_data_subdirectory() -> None:
    """A cache directory below data is valid for application-managed state."""
    settings = Settings(data_dir=Path("data/cache"))

    assert settings.data_dir == (PROJECT_ROOT / "data/cache").resolve()
    assert settings.terminal_workspace_path == (PROJECT_ROOT / "data/cache/workspace").resolve()


def test_settings_reject_paths_outside_data() -> None:
    """Configuration cannot redirect writable state into an arbitrary path."""
    with pytest.raises(ValidationError, match="LVA_DATA_DIR must be inside"):
        Settings(data_dir=Path("/tmp/local-voice-agent-escape"))


def test_settings_reject_non_https_or_path_public_origins() -> None:
    """Browser mutation checks require one exact HTTPS origin rather than a URL prefix."""
    with pytest.raises(ValidationError, match="LVA_PUBLIC_ORIGIN must be a bare HTTPS origin"):
        Settings(public_origin="http://voice.test:8443")
    with pytest.raises(ValidationError, match="LVA_PUBLIC_ORIGIN must be a bare HTTPS origin"):
        Settings(public_origin="https://voice.test:8443/application")


def test_settings_default_to_disabled_loopback_search_and_reject_remote_endpoints() -> None:
    """Internet access remains opt-in and cannot route through a LAN or remote URL."""
    settings = Settings()

    assert not settings.search_enabled
    assert settings.searxng_endpoint == "http://127.0.0.1:8080"
    with pytest.raises(ValidationError, match="LVA_SEARXNG_ENDPOINT must be a bare HTTP loopback"):
        Settings(searxng_endpoint="http://192.168.10.20:8080")
