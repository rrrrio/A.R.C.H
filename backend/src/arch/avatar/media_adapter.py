"""Per-voice-session adapter that submits whole answers to AvatarMediaRuntime."""

from __future__ import annotations

from arch.avatar.media import AvatarMediaService, AvatarMediaUnavailable


class AvatarMediaAdapter:
    """Buffer one complete 24 kHz answer and retain direct-audio fallback control."""

    def __init__(self, service: AvatarMediaService, session_id: str) -> None:
        self._service = service
        self._session_id = session_id
        self._audio: dict[str, bytearray] = {}

    def is_active(self) -> bool:
        """Enable buffering only while this authenticated browser owns a media peer."""
        return self._service.is_active(self._session_id)

    async def begin(self, utterance_id: str, sample_rate: int, emotion: str) -> None:
        """Start a complete-answer buffer at the runtime's fixed playback rate."""
        del emotion
        if sample_rate != 24_000:
            raise AvatarMediaUnavailable("Avatar media requires 24 kHz PCM.")
        self._audio[utterance_id] = bytearray()

    async def push_audio(self, utterance_id: str, audio: bytes, timestamp_ns: int) -> None:
        """Keep PCM in backend memory until the TTS answer has completed."""
        del timestamp_ns
        if utterance_id not in self._audio:
            raise AvatarMediaUnavailable("Avatar media utterance was not started.")
        self._audio[utterance_id].extend(audio)

    async def push_event(self, utterance_id: str, event: str) -> None:
        """The first Wav2Lip backend has no expressive-event contract."""

    async def cancel(self, utterance_id: str) -> None:
        """Fence prepared or playing media and discard its unsubmitted PCM."""
        self._audio.pop(utterance_id, None)
        await self._service.cancel_utterance(self._session_id, utterance_id)

    async def end(self, utterance_id: str) -> bool:
        """Submit one complete answer, returning false for synchronous direct fallback."""
        audio = self._audio.pop(utterance_id, None)
        if audio is None or not self.is_active():
            return False
        try:
            await self._service.submit_utterance(self._session_id, utterance_id, bytes(audio))
        except AvatarMediaUnavailable:
            return False
        return True
