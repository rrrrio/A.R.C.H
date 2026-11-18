"""Authenticated APIs for local conversational style profile management."""

from __future__ import annotations

from typing import Annotated, TypedDict, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from arch.api.auth import AuthServiceDependency
from arch.conversation.profiles import (
    MAX_PROFILE_EXAMPLE_CHARS,
    MAX_PROFILE_EXAMPLES,
    MAX_PROFILE_NAME_CHARS,
    MAX_PROFILE_PERSONA_CHARS,
    MAX_PROFILE_STYLE_CHARS,
    ActiveGreetingPolicy,
    ConversationExample,
    ConversationProfile,
    ConversationProfileStore,
)

router = APIRouter(prefix="/api/conversation", tags=["conversation"])


class ExamplePayload(BaseModel):
    """One user-provided pair that is constrained to style, not executable policy."""

    model_config = ConfigDict(extra="forbid")

    user: str = Field(min_length=1, max_length=MAX_PROFILE_EXAMPLE_CHARS)
    assistant: str = Field(min_length=1, max_length=MAX_PROFILE_EXAMPLE_CHARS)


class ProfileWritePayload(BaseModel):
    """Validated browser fields for creating or replacing a conversation profile."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_PROFILE_NAME_CHARS)
    enabled: bool = True
    persona: str = Field(min_length=1, max_length=MAX_PROFILE_PERSONA_CHARS)
    speaking_style: str = Field(min_length=1, max_length=MAX_PROFILE_STYLE_CHARS)
    examples: list[ExamplePayload] = Field(default_factory=list, max_length=MAX_PROFILE_EXAMPLES)
    social_temperature: float = Field(ge=0.2, le=0.8)


class SelectionPayload(BaseModel):
    """One selected opaque ID or the neutral baseline represented by null."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str | None = None


class ActiveGreetingPayload(BaseModel):
    """Explicit, bounded opt-in policy for one selected conversation profile."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    minimum_idle_minutes: int = Field(ge=5, le=60)


class ProfileResponse(BaseModel):
    """Browser-safe persisted profile fields without hidden prompt instructions."""

    id: str
    name: str
    enabled: bool
    persona: str
    speaking_style: str
    examples: list[ExamplePayload]
    social_temperature: float
    version: int
    created_at: str
    updated_at: str


class ProfilesResponse(BaseModel):
    """Available profiles and the profile which new sessions will snapshot."""

    selected_id: str | None
    profiles: list[ProfileResponse]


class _ProfileWriteArguments(TypedDict):
    """The exact validated keyword set accepted by the persistent store."""

    name: str
    enabled: bool
    persona: str
    speaking_style: str
    examples: tuple[ConversationExample, ...]
    social_temperature: float


def get_conversation_profile_store(request: Request) -> ConversationProfileStore:
    """Resolve the app-owned local profile repository."""
    return cast(ConversationProfileStore, request.app.state.conversation_profile_store)


ConversationProfileStoreDependency = Annotated[
    ConversationProfileStore, Depends(get_conversation_profile_store)
]


@router.get("/profiles", response_model=ProfilesResponse)
def list_profiles(
    request: Request,
    auth: AuthServiceDependency,
    store: ConversationProfileStoreDependency,
) -> ProfilesResponse:
    """List local profiles only after the administrator session is authenticated."""
    auth.require_session(request)
    return ProfilesResponse(
        selected_id=store.selected_id(),
        profiles=[_profile_response(profile) for profile in store.list()],
    )


@router.post("/profiles", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
def create_profile(
    payload: ProfileWritePayload,
    request: Request,
    auth: AuthServiceDependency,
    store: ConversationProfileStoreDependency,
) -> ProfileResponse:
    """Create a server-owned profile after the browser proves same-origin intent."""
    auth.require_csrf(request)
    try:
        profile = store.create(**_write_arguments(payload))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _profile_response(profile)


@router.put("/profiles/{profile_id}", response_model=ProfileResponse)
def replace_profile(
    profile_id: str,
    payload: ProfileWritePayload,
    request: Request,
    auth: AuthServiceDependency,
    store: ConversationProfileStoreDependency,
) -> ProfileResponse:
    """Replace one profile atomically without accepting client-owned timestamps or versions."""
    auth.require_csrf(request)
    try:
        profile = store.replace(profile_id, **_write_arguments(payload))
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Unknown conversation profile.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _profile_response(profile)


@router.put("/selection", response_model=ProfilesResponse)
def select_profile(
    payload: SelectionPayload,
    request: Request,
    auth: AuthServiceDependency,
    store: ConversationProfileStoreDependency,
) -> ProfilesResponse:
    """Choose a profile for the next session or return to the neutral baseline."""
    auth.require_csrf(request)
    try:
        selected_id = store.select(payload.profile_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Unknown conversation profile.") from error
    return ProfilesResponse(
        selected_id=selected_id, profiles=[_profile_response(profile) for profile in store.list()]
    )


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(
    profile_id: str,
    request: Request,
    auth: AuthServiceDependency,
    store: ConversationProfileStoreDependency,
) -> None:
    """Delete a profile and clear its selection within the same local transaction."""
    auth.require_csrf(request)
    try:
        store.delete(profile_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Unknown conversation profile.") from error


@router.put("/profiles/{profile_id}/active-greeting", response_model=ActiveGreetingPayload)
def replace_active_greeting_policy(
    profile_id: str,
    payload: ActiveGreetingPayload,
    request: Request,
    auth: AuthServiceDependency,
    store: ConversationProfileStoreDependency,
) -> ActiveGreetingPayload:
    """Explicitly enable, disable, or bound greetings for a future media-session snapshot."""
    auth.require_csrf(request)
    try:
        policy = store.replace_active_greeting_policy(
            profile_id,
            ActiveGreetingPolicy(**payload.model_dump()),
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Unknown conversation profile.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ActiveGreetingPayload(
        enabled=policy.enabled,
        minimum_idle_minutes=policy.minimum_idle_minutes,
    )


@router.get("/profiles/{profile_id}/active-greeting", response_model=ActiveGreetingPayload)
def get_active_greeting_policy(
    profile_id: str,
    request: Request,
    auth: AuthServiceDependency,
    store: ConversationProfileStoreDependency,
) -> ActiveGreetingPayload:
    """Read the bounded opt-in policy without exposing any media-session internals."""
    auth.require_session(request)
    if store.get(profile_id) is None:
        raise HTTPException(status_code=404, detail="Unknown conversation profile.")
    policy = store.active_greeting_policy(profile_id)
    return ActiveGreetingPayload(
        enabled=policy.enabled,
        minimum_idle_minutes=policy.minimum_idle_minutes,
    )


def _write_arguments(payload: ProfileWritePayload) -> _ProfileWriteArguments:
    return {
        "name": payload.name,
        "enabled": payload.enabled,
        "persona": payload.persona,
        "speaking_style": payload.speaking_style,
        "examples": tuple(
            ConversationExample(example.user, example.assistant) for example in payload.examples
        ),
        "social_temperature": payload.social_temperature,
    }


def _profile_response(profile: ConversationProfile) -> ProfileResponse:
    return ProfileResponse(
        id=profile.id,
        name=profile.name,
        enabled=profile.enabled,
        persona=profile.persona,
        speaking_style=profile.speaking_style,
        examples=[
            ExamplePayload(user=example.user, assistant=example.assistant)
            for example in profile.examples
        ],
        social_temperature=profile.social_temperature,
        version=profile.version,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )
