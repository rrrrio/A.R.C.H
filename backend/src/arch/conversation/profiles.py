"""Validated SQLite storage for bounded conversational style profiles."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from arch.memory.migrations import MigrationRunner

MAX_PROFILE_NAME_CHARS = 80
MAX_PROFILE_PERSONA_CHARS = 2000
MAX_PROFILE_STYLE_CHARS = 600
MAX_PROFILE_EXAMPLES = 6
MAX_PROFILE_EXAMPLE_CHARS = 400
MIN_SOCIAL_TEMPERATURE = 0.2
MAX_SOCIAL_TEMPERATURE = 0.8


@dataclass(frozen=True, slots=True)
class ConversationExample:
    """One bounded user/assistant exchange used only as a style example."""

    user: str
    assistant: str


@dataclass(frozen=True, slots=True)
class ConversationProfile:
    """One selected-or-available profile that cannot carry executable policy."""

    id: str
    name: str
    enabled: bool
    persona: str
    speaking_style: str
    examples: tuple[ConversationExample, ...]
    social_temperature: float
    version: int
    created_at: str
    updated_at: str


class ConversationProfileStore:
    """Own SQLite profile settings separately from conversation memory approval semantics."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def migrate(self) -> tuple[int, ...]:
        """Apply the shared append-only schema before profile access."""
        return MigrationRunner(self._database_path).migrate()

    def list(self) -> tuple[ConversationProfile, ...]:
        """Return profiles in stable newest-first order without selecting one implicitly."""
        with self._connect(readonly=True) as connection:
            rows = connection.execute(
                """
                SELECT id, name, enabled, persona, speaking_style, examples_json,
                       social_temperature, version, created_at, updated_at
                FROM conversation_profiles ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
        return tuple(_profile(row) for row in rows)

    def get(self, profile_id: str) -> ConversationProfile | None:
        """Find one opaque UUID profile without treating it as a filesystem path."""
        if not _is_uuid(profile_id):
            return None
        with self._connect(readonly=True) as connection:
            row = connection.execute(
                """
                SELECT id, name, enabled, persona, speaking_style, examples_json,
                       social_temperature, version, created_at, updated_at
                FROM conversation_profiles WHERE id = ?
                """,
                (profile_id,),
            ).fetchone()
        return _profile(row) if row is not None else None

    def selected_id(self) -> str | None:
        """Return the selected profile ID, or None for the immutable neutral baseline."""
        with self._connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT profile_id FROM conversation_profile_selection WHERE singleton = 1"
            ).fetchone()
        return row[0] if row is not None and isinstance(row[0], str) else None

    def selected(self) -> ConversationProfile | None:
        """Resolve the chosen profile, clearing a stale selection defensively if necessary."""
        profile_id = self.selected_id()
        return self.get(profile_id) if profile_id is not None else None

    def create(
        self,
        *,
        name: str,
        persona: str,
        speaking_style: str,
        examples: tuple[ConversationExample, ...],
        social_temperature: float,
        enabled: bool = True,
    ) -> ConversationProfile:
        """Store one validated profile with server-owned ID, timestamps, and version."""
        normalized = _validated_fields(name, persona, speaking_style, examples, social_temperature)
        encoded_examples = _encode_examples(normalized[3])
        profile_id = str(uuid.uuid4())
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversation_profiles(
                    id, name, enabled, persona, speaking_style, examples_json,
                    social_temperature, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    profile_id,
                    normalized[0],
                    int(enabled),
                    normalized[1],
                    normalized[2],
                    encoded_examples,
                    normalized[4],
                    now,
                    now,
                ),
            )
        return ConversationProfile(
            profile_id,
            normalized[0],
            enabled,
            normalized[1],
            normalized[2],
            normalized[3],
            normalized[4],
            1,
            now,
            now,
        )

    def replace(
        self,
        profile_id: str,
        *,
        name: str,
        persona: str,
        speaking_style: str,
        examples: tuple[ConversationExample, ...],
        social_temperature: float,
        enabled: bool,
    ) -> ConversationProfile:
        """Atomically update profile content and increment the visible version."""
        if not _is_uuid(profile_id):
            raise KeyError("Unknown conversation profile.")
        normalized = _validated_fields(name, persona, speaking_style, examples, social_temperature)
        encoded_examples = _encode_examples(normalized[3])
        now = _now()
        with self._connect() as connection:
            existing = self._for_update(connection, profile_id)
            version = existing.version + 1
            connection.execute(
                """
                UPDATE conversation_profiles
                SET name = ?, enabled = ?, persona = ?, speaking_style = ?, examples_json = ?,
                    social_temperature = ?, version = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    normalized[0],
                    int(enabled),
                    normalized[1],
                    normalized[2],
                    encoded_examples,
                    normalized[4],
                    version,
                    now,
                    profile_id,
                ),
            )
        return ConversationProfile(
            profile_id,
            normalized[0],
            enabled,
            normalized[1],
            normalized[2],
            normalized[3],
            normalized[4],
            version,
            existing.created_at,
            now,
        )

    def select(self, profile_id: str | None) -> str | None:
        """Select a profile or explicitly return future sessions to the neutral baseline."""
        with self._connect() as connection:
            if profile_id is not None:
                self._for_update(connection, profile_id)
            connection.execute(
                """
                INSERT INTO conversation_profile_selection(singleton, profile_id, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET profile_id = excluded.profile_id,
                    updated_at = excluded.updated_at
                """,
                (profile_id, _now()),
            )
        return profile_id

    def delete(self, profile_id: str) -> None:
        """Delete a known profile and atomically clear selection before its foreign key changes."""
        with self._connect() as connection:
            self._for_update(connection, profile_id)
            connection.execute(
                "UPDATE conversation_profile_selection SET profile_id = NULL, updated_at = ? "
                "WHERE singleton = 1 AND profile_id = ?",
                (_now(), profile_id),
            )
            connection.execute("DELETE FROM conversation_profiles WHERE id = ?", (profile_id,))

    @staticmethod
    def _for_update(connection: sqlite3.Connection, profile_id: str) -> ConversationProfile:
        if not _is_uuid(profile_id):
            raise KeyError("Unknown conversation profile.")
        row = connection.execute(
            """
            SELECT id, name, enabled, persona, speaking_style, examples_json,
                   social_temperature, version, created_at, updated_at
            FROM conversation_profiles WHERE id = ?
            """,
            (profile_id,),
        ).fetchone()
        if row is None:
            raise KeyError("Unknown conversation profile.")
        return _profile(row)

    def _connect(self, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            connection = sqlite3.connect(f"file:{self._database_path}?mode=ro", uri=True)
        else:
            connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _validated_fields(
    name: str,
    persona: str,
    speaking_style: str,
    examples: tuple[ConversationExample, ...],
    social_temperature: float,
) -> tuple[str, str, str, tuple[ConversationExample, ...], float]:
    return (
        _text(name, MAX_PROFILE_NAME_CHARS, "Name"),
        _text(persona, MAX_PROFILE_PERSONA_CHARS, "Persona"),
        _text(speaking_style, MAX_PROFILE_STYLE_CHARS, "Speaking style"),
        _examples(examples),
        _temperature(social_temperature),
    )


def _text(value: str, maximum: int, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} cannot be empty.")
    if len(normalized) > maximum:
        raise ValueError(f"{label} cannot exceed {maximum} characters.")
    return normalized


def _examples(value: tuple[ConversationExample, ...]) -> tuple[ConversationExample, ...]:
    if len(value) > MAX_PROFILE_EXAMPLES:
        raise ValueError(
            f"Conversation profiles can contain at most {MAX_PROFILE_EXAMPLES} examples."
        )
    return tuple(
        ConversationExample(
            _text(example.user, MAX_PROFILE_EXAMPLE_CHARS, "Example user text"),
            _text(example.assistant, MAX_PROFILE_EXAMPLE_CHARS, "Example assistant text"),
        )
        for example in value
    )


def _temperature(value: float) -> float:
    if not MIN_SOCIAL_TEMPERATURE <= value <= MAX_SOCIAL_TEMPERATURE:
        raise ValueError(
            "Social temperature must be between "
            f"{MIN_SOCIAL_TEMPERATURE} and {MAX_SOCIAL_TEMPERATURE}."
        )
    return value


def _encode_examples(examples: tuple[ConversationExample, ...]) -> str:
    return json.dumps(
        [{"user": example.user, "assistant": example.assistant} for example in examples],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _profile(row: tuple[object, ...]) -> ConversationProfile:
    (
        profile_id,
        name,
        enabled,
        persona,
        speaking_style,
        examples_json,
        social_temperature,
        version,
        created_at,
        updated_at,
    ) = row
    if not (
        isinstance(profile_id, str)
        and _is_uuid(profile_id)
        and isinstance(name, str)
        and enabled in (0, 1)
        and isinstance(persona, str)
        and isinstance(speaking_style, str)
        and isinstance(examples_json, str)
        and isinstance(social_temperature, float)
        and isinstance(version, int)
        and isinstance(created_at, str)
        and isinstance(updated_at, str)
    ):
        raise RuntimeError("SQLite returned an invalid conversation profile row.")
    try:
        decoded = json.loads(examples_json)
        examples = tuple(
            ConversationExample(item["user"], item["assistant"])
            for item in decoded
            if isinstance(item, dict)
            and isinstance(item.get("user"), str)
            and isinstance(item.get("assistant"), str)
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("SQLite returned invalid conversation profile examples.") from error
    if len(examples) != len(decoded):
        raise RuntimeError("SQLite returned invalid conversation profile examples.")
    validated = _validated_fields(name, persona, speaking_style, examples, social_temperature)
    return ConversationProfile(
        profile_id,
        validated[0],
        bool(enabled),
        validated[1],
        validated[2],
        validated[3],
        validated[4],
        version,
        created_at,
        updated_at,
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False
