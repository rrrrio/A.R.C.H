"""Chinese phonemization adapter for the local Kokoro ONNX voice."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, cast

import numpy as np
from misaki import zh
from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.kokoro.tts import KokoroTTSService, KokoroTTSSettings
from pipecat.utils.tracing.service_decorators import traced_tts
from pipecat.utils.types import assert_given, require_given


class ChineseKokoroTTSService(KokoroTTSService):
    """Use Misaki v1.1 phonemes for the pinned Chinese model."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._chinese_g2p = zh.ZHG2P(version="1.1")  # type: ignore[no-untyped-call]

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        """Yield 24 kHz PCM for one Chinese text context."""
        try:
            await self.start_tts_usage_metrics(text)
            phonemes, _ = self._chinese_g2p(text)
            settings = cast(KokoroTTSSettings, self._settings)
            stream = self._kokoro.create_stream(
                phonemes,
                voice=require_given(settings.voice, "Kokoro TTS voice"),
                speed=assert_given(settings.speed),
                is_phonemes=True,
            )
            async for samples, sample_rate in stream:
                await self.stop_ttfb_metrics()
                audio = await self._resampler.resample(
                    (samples * 32767).astype(np.int16).tobytes(), sample_rate, self.sample_rate
                )
                yield TTSAudioRawFrame(audio, self.sample_rate, 1, context_id=context_id)
        except Exception as error:
            yield ErrorFrame(error=f"Chinese Kokoro synthesis failed: {error}")
        finally:
            await self.stop_ttfb_metrics()
