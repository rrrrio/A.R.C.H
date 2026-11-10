"""Authenticated voice-profile selection and reference upload endpoints."""

from __future__ import annotations

import base64
import binascii
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from arch.api.auth import AuthServiceDependency
from arch.voice.profiles import (
    MAX_PROFILE_NAME_CHARS,
    MAX_REFERENCE_BYTES,
    MAX_REFERENCE_TRANSCRIPT_CHARS,
    VoiceProfile,
    VoiceProfileStore,
)

router = APIRouter(prefix="/api/voices", tags=["voices"])


class VoiceProfileResponse(BaseModel):
    """Browser-safe metadata for a local profile, excluding file paths and audio."""

    id: str
    name: str
    transcript: str
    sample_rate: int
    duration_ms: int
    created_at: str


class VoiceProfilesResponse(BaseModel):
    """Available profiles and the explicitly selected clone profile, if any."""

    selected_id: str | None
    profiles: list[VoiceProfileResponse]


class VoiceProfileCreateRequest(BaseModel):
    """JSON upload payload with a bounded base64-encoded local WAV reference."""

    name: str = Field(min_length=1, max_length=MAX_PROFILE_NAME_CHARS)
    transcript: str = Field(min_length=1, max_length=MAX_REFERENCE_TRANSCRIPT_CHARS)
    reference_wav_base64: str = Field(min_length=1, max_length=MAX_REFERENCE_BYTES * 2)
    ownership_confirmed: bool


class VoiceSelectionRequest(BaseModel):
    """Select one clone profile or `null` to restore the Kokoro baseline."""

    profile_id: str | None = None


def get_voice_profile_store(request: Request) -> VoiceProfileStore:
    """Read the application-scoped local profile repository."""
    return cast(VoiceProfileStore, request.app.state.voice_profile_store)


VoiceProfileStoreDependency = Annotated[VoiceProfileStore, Depends(get_voice_profile_store)]


@router.get("/profiles", response_model=VoiceProfilesResponse)
def profiles(
    request: Request, auth: AuthServiceDependency, store: VoiceProfileStoreDependency
) -> VoiceProfilesResponse:
    """List metadata only for the authenticated administrator's local voice profiles."""
    auth.require_session(request)
    return VoiceProfilesResponse(
        selected_id=store.selected_id(),
        profiles=[_profile_response(profile) for profile in store.list()],
    )


@router.post("/profiles", response_model=VoiceProfileResponse, status_code=status.HTTP_201_CREATED)
def create_profile(
    payload: VoiceProfileCreateRequest,
    request: Request,
    auth: AuthServiceDependency,
    store: VoiceProfileStoreDependency,
) -> VoiceProfileResponse:
    """Store one consented local WAV profile after CSRF validation."""
    auth.require_csrf(request)
    try:
        reference_wav = base64.b64decode(payload.reference_wav_base64, validate=True)
        profile = store.create(
            payload.name,
            payload.transcript,
            reference_wav,
            ownership_confirmed=payload.ownership_confirmed,
        )
    except (ValueError, binascii.Error) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _profile_response(profile)


@router.put("/selection", response_model=VoiceProfilesResponse)
def select_profile(
    payload: VoiceSelectionRequest,
    request: Request,
    auth: AuthServiceDependency,
    store: VoiceProfileStoreDependency,
) -> VoiceProfilesResponse:
    """Select exactly one clone profile or return the next session to the Kokoro baseline."""
    auth.require_csrf(request)
    try:
        selected_id = store.select(payload.profile_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Unknown voice profile.") from error
    return VoiceProfilesResponse(
        selected_id=selected_id, profiles=[_profile_response(profile) for profile in store.list()]
    )


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(
    profile_id: str,
    request: Request,
    auth: AuthServiceDependency,
    store: VoiceProfileStoreDependency,
) -> None:
    """Remove one local profile after CSRF validation without accepting a path."""
    auth.require_csrf(request)
    try:
        store.delete(profile_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Unknown voice profile.") from error


def _profile_response(profile: VoiceProfile) -> VoiceProfileResponse:
    return VoiceProfileResponse(
        id=profile.id,
        name=profile.name,
        transcript=profile.transcript,
        sample_rate=profile.sample_rate,
        duration_ms=profile.duration_ms,
        created_at=profile.created_at,
    )
