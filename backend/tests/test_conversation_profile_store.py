"""Tests for persistent, bounded conversation profile settings."""

from pathlib import Path

import pytest

from arch.conversation.profiles import (
    MAX_PROFILE_PERSONA_CHARS,
    ActiveGreetingPolicy,
    ConversationExample,
    ConversationProfileStore,
)


def test_profile_store_persists_selection_and_resets_to_the_neutral_baseline(
    tmp_path: Path,
) -> None:
    """Deleting the selected profile cannot leave a stale profile identifier behind."""
    store = ConversationProfileStore(tmp_path / "voice.sqlite3")
    assert store.migrate() == (1, 2, 3, 4, 5, 6, 7, 8, 9)

    profile = store.create(
        name="小雅",
        persona="温和、真诚地回应用户的具体内容。",
        speaking_style="口语化，每次回答一到三句。",
        examples=(ConversationExample("今天有点累", "辛苦了呀，今天最累的是哪一段？"),),
        social_temperature=0.7,
    )
    assert store.selected_id() is None
    assert store.select(profile.id) == profile.id
    assert store.selected().id == profile.id

    store.delete(profile.id)

    assert store.selected_id() is None
    assert store.selected() is None
    assert store.list() == ()


def test_profile_store_rejects_oversized_or_untrusted_profile_fields(tmp_path: Path) -> None:
    """Profile content stays within the prompt budget before SQLite receives it."""
    store = ConversationProfileStore(tmp_path / "voice.sqlite3")
    store.migrate()

    with pytest.raises(ValueError, match="Persona cannot exceed"):
        store.create(
            name="测试",
            persona="x" * (MAX_PROFILE_PERSONA_CHARS + 1),
            speaking_style="简洁",
            examples=(),
            social_temperature=0.7,
        )
    with pytest.raises(ValueError, match="between 0.2 and 0.8"):
        store.create(
            name="测试",
            persona="正常",
            speaking_style="简洁",
            examples=(),
            social_temperature=0.9,
        )


def test_profile_store_persists_only_bounded_active_greeting_policy(tmp_path: Path) -> None:
    store = ConversationProfileStore(tmp_path / "voice.sqlite3")
    store.migrate()
    profile = store.create(
        name="测试", persona="正常", speaking_style="简洁", examples=(), social_temperature=0.7
    )

    assert store.active_greeting_policy(profile.id).enabled is False
    policy = ActiveGreetingPolicy(True, 15)
    assert store.replace_active_greeting_policy(profile.id, policy) == policy
    assert store.active_greeting_policy(profile.id) == policy
    with pytest.raises(ValueError, match="Minimum idle time"):
        store.replace_active_greeting_policy(profile.id, ActiveGreetingPolicy(True, 61))
