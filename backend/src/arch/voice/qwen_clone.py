"""Pipecat boundary for a selected Qwen voice-clone synthesis runtime."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable

from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService
from pipecat.transcriptions.language import Language
from pipecat.utils.tracing.service_decorators import traced_tts

from arch.voice.profiles import VoiceProfile

QWEN_OUTPUT_SAMPLE_RATE = 24000
type CloneSynthesize = Callable[[str, VoiceProfile], Awaitable[tuple[bytes, int]]]


class QwenCloneUnavailable(RuntimeError):
    """Indicate that an explicitly selected clone mode cannot be started safely."""


class QwenCloneTTSService(TTSService):
    """Adapt a selected local Qwen profile runtime to Pipecat's normal TTS frames."""

    def __init__(self, profile: VoiceProfile, synthesize: CloneSynthesize) -> None:
        self._profile = profile
        self._synthesize = synthesize
        super().__init__(
            push_start_frame=True,
            push_stop_frames=True,
            sample_rate=QWEN_OUTPUT_SAMPLE_RATE,
            settings=TTSSettings(
                model="Qwen3-TTS-12Hz-0.6B-Base", voice=profile.id, language=Language.ZH
            ),
        )

    def can_generate_metrics(self) -> bool:
        """Report that this adapter participates in Pipecat first-byte metrics."""
        return True

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        """Synthesize one profile-bound utterance and reject incompatible PCM formats."""
        try:
            await self.start_tts_usage_metrics(text)
            audio, sample_rate = await self._synthesize(text, self._profile)
            if sample_rate != QWEN_OUTPUT_SAMPLE_RATE:
                raise QwenCloneUnavailable(
                    "Qwen clone runtime returned "
                    f"{sample_rate} Hz instead of {QWEN_OUTPUT_SAMPLE_RATE} Hz."
                )
            if not audio or len(audio) % 2:
                raise QwenCloneUnavailable("Qwen clone runtime returned invalid PCM audio.")
            await self.stop_ttfb_metrics()
            yield TTSAudioRawFrame(
                audio=audio,
                sample_rate=QWEN_OUTPUT_SAMPLE_RATE,
                num_channels=1,
                context_id=context_id,
            )
        except QwenCloneUnavailable as error:
            yield ErrorFrame(error=f"Qwen clone mode unavailable: {error}")
        except Exception:
            yield ErrorFrame(error="Qwen clone synthesis failed.")
        finally:
            await self.stop_ttfb_metrics()
