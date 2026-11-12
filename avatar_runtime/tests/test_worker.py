"""Regression coverage for the dependency-isolated preview renderer."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

import av
import numpy as np

from avatar_runtime.worker import PreviewRenderer


class PreviewRendererTests(unittest.TestCase):
    """Verify that the runtime can decode an MP4 and encode a browser frame alone."""

    def test_decodes_fixed_mp4_to_jpeg(self) -> None:
        """The isolated environment owns every dependency needed for preview frames."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "avatar.mp4"
            self._write_source(source)

            renderer = PreviewRenderer(source, fps=25)
            frame = asyncio.run(anext(renderer.frames()))

        self.assertTrue(frame.startswith(b"\xff\xd8"))

    @staticmethod
    def _write_source(destination: Path) -> None:
        """Create a minimal MP4 fixture without relying on repository media files."""
        with av.open(destination, mode="w") as container:
            stream = container.add_stream("mpeg4", rate=25)
            stream.width = 16
            stream.height = 16
            stream.pix_fmt = "yuv420p"
            source = np.zeros((16, 16, 3), dtype=np.uint8)
            source[:, :, 1] = 180
            frame = av.VideoFrame.from_ndarray(source, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
