"""Regression coverage for the renderer's PCM boundary."""

from __future__ import annotations

import unittest

import numpy as np

from avatar_runtime.audio import StreamingPcmResampler


class StreamingPcmResamplerTests(unittest.TestCase):
    """Check the 24 kHz TTS to 16 kHz Whisper input conversion."""

    def test_chunked_input_matches_one_continuous_timeline(self) -> None:
        """Transport chunking cannot change the samples seen by the renderer."""
        samples = np.arange(-1200, 1200, dtype="<i2").tobytes()
        whole = StreamingPcmResampler(24000, 16000).convert(samples)
        chunked = StreamingPcmResampler(24000, 16000)
        pieces = [samples[:734], samples[734:1986], samples[1986:]]

        self.assertEqual(b"".join(chunked.convert(piece) for piece in pieces), whole)

    def test_rejects_partial_pcm_sample(self) -> None:
        """The worker must not silently desynchronize malformed IPC audio."""
        with self.assertRaisesRegex(ValueError, "16-bit"):
            StreamingPcmResampler(24000, 16000).convert(b"\x01")
