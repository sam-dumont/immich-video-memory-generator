"""Offline numerical regression against the pinned Docling model, when cached.

Synthetic images reproduced the J4125 layout-optimizer failure without private
library fixtures. Keep this runnable with unittest in the small NAS image too.
"""

import unittest

import numpy as np
from PIL import Image

from immich_memories.analysis.editorial_preparation_detectors import Docling


class DoclingPortabilityTests(unittest.TestCase):
    def test_cached_detector_preserves_reference_predictions(self):
        try:
            import onnxruntime  # noqa: F401
            from huggingface_hub.errors import LocalEntryNotFoundError
        except ImportError:
            self.skipTest("requires the editorial inference dependencies")
        try:
            detector = Docling(allow_downloads=False, cache_dir=None)
        except LocalEntryNotFoundError:
            self.skipTest("requires the pinned Docling model already in the HF cache")

        rng = np.random.default_rng(42)
        images = [
            Image.new("RGB", (224, 224), "white"),
            Image.new("RGB", (224, 224), "black"),
            Image.fromarray(rng.integers(0, 256, (224, 224, 3), dtype=np.uint8)),
        ]
        probabilities = detector.batch(images)

        # Reference: the unfused pinned graph agrees on both arm64 and J4125.
        # The broken NAS graph returned table at ~0.05295 for all three inputs.
        self.assertEqual(
            [detector.classes[index] for index in probabilities.argmax(-1)],
            ["table", "table", "other"],
        )
        np.testing.assert_allclose(
            probabilities.max(-1), [0.862082, 0.943777, 0.798308], rtol=0, atol=1e-4
        )
