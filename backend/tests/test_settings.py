"""Tests for local writable-path boundaries."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from local_voice_agent.settings import PROJECT_ROOT, Settings


def test_settings_allow_a_data_subdirectory() -> None:
    """A cache directory below data is valid for application-managed state."""
    settings = Settings(data_dir=Path("data/cache"))

    assert settings.data_dir == (PROJECT_ROOT / "data/cache").resolve()


def test_settings_reject_paths_outside_data() -> None:
    """Configuration cannot redirect writable state into an arbitrary path."""
    with pytest.raises(ValidationError, match="LVA_DATA_DIR must be inside"):
        Settings(data_dir=Path("/tmp/local-voice-agent-escape"))
