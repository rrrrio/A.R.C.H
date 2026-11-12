"""Audio primitives shared by the fixed-video renderer backends."""

from __future__ import annotations

import numpy as np


class StreamingPcmResampler:
    """Resample mono signed 16-bit PCM while preserving chunk-boundary timing.

    MuseTalk's Whisper encoder consumes 16 kHz audio, while A.R.C.H. publishes
    24 kHz TTS PCM. The renderer receives arbitrary transport chunk sizes, so
    each output sample is interpolated from the continuous input timeline rather
    than treating every IPC message as an independent signal.
    """

    def __init__(self, source_rate: int, target_rate: int) -> None:
        if source_rate < 1 or target_rate < 1:
            raise ValueError("PCM sample rates must be positive.")
        self._source_rate = source_rate
        self._target_rate = target_rate
        self._next_position_numerator = 0
        self._received_samples = 0
        self._last_sample: int | None = None

    def convert(self, pcm: bytes) -> bytes:
        """Convert one ordered PCM chunk without emitting partial interpolants."""
        if len(pcm) % 2:
            raise ValueError("PCM must contain complete signed 16-bit samples.")
        if not pcm:
            return b""
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        start = self._received_samples
        if self._last_sample is not None:
            samples = np.concatenate((np.array([self._last_sample]), samples))
            start -= 1
        end = start + len(samples) - 1
        positions: list[int] = []
        end_numerator = end * self._target_rate
        start_numerator = start * self._target_rate
        while (
            self._next_position_numerator // self._target_rate + 1 <= end
        ):
            if self._next_position_numerator >= start_numerator:
                positions.append(self._next_position_numerator)
            self._next_position_numerator += self._source_rate
        self._received_samples += len(pcm) // 2
        self._last_sample = int(samples[-1])
        if not positions:
            return b""
        local = np.asarray(positions) - start_numerator
        low = (local // self._target_rate).astype(np.intp)
        fraction = (local % self._target_rate) / self._target_rate
        output = samples[low] * (1 - fraction) + samples[low + 1] * fraction
        return np.rint(output).clip(-32768, 32767).astype("<i2").tobytes()
