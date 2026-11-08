"""Typed boundary between media transcription and future local agent implementations."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """One finalized conversation message available to the local agent."""

    role: Literal["user", "assistant", "system"]
    text: str
    utterance_id: str


@dataclass(frozen=True, slots=True)
class ToolRequest:
    """A requested tool invocation that must pass the later policy boundary."""

    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """The fully local input for one assistant response generation."""

    utterance_id: str
    transcript: str
    language: str
    history: tuple[ConversationTurn, ...] = field(default_factory=tuple)
    approved_memories: tuple[str, ...] = field(default_factory=tuple)


class AgentResponseKind(StrEnum):
    """Allowed event classes emitted by the application agent boundary."""

    TEXT = "text"
    TOOL_REQUEST = "tool_request"
    EMOTION = "emotion"
    END = "end"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AgentResponseEvent:
    """A stream event that remains associated with its originating utterance."""

    utterance_id: str
    kind: AgentResponseKind
    text: str = ""
    tool_request: ToolRequest | None = None


class AgentCore(Protocol):
    """Produce local response events without taking ownership of media transports."""

    def respond(self, request: AgentRequest) -> AsyncIterator[AgentResponseEvent]:
        """Yield response events for one utterance until completion or cancellation."""
