"""Tests for fixed-video preprocessing cache identity."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from avatar_runtime.cache import AvatarCacheIdentity


class AvatarCacheIdentityTests(unittest.TestCase):
    """A cache key must change whenever preprocessing inputs change."""

    def test_rejects_manifest_from_another_fixed_video(self) -> None:
        """A same-named replacement video cannot reuse stale face artifacts."""
        first = AvatarCacheIdentity("a" * 64, "0a89dec", 25, 0, 10, "jaw")
        second = AvatarCacheIdentity("b" * 64, "0a89dec", 25, 0, 10, "jaw")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / first.key
            first.write_manifest(directory)
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                second.write_manifest(directory)

    def test_changes_key_when_preprocessing_changes(self) -> None:
        """Bounding-box tuning is part of the cache correctness boundary."""
        base = AvatarCacheIdentity("a" * 64, "0a89dec", 25, 0, 10, "jaw")
        changed = AvatarCacheIdentity("a" * 64, "0a89dec", 25, 1, 10, "jaw")
        self.assertNotEqual(base.key, changed.key)
