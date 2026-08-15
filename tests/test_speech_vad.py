"""Tests for VAD-derived speech regions. No mocks — synthetic audio only."""

from __future__ import annotations

import numpy as np

from immich_memories.speech.models import SpeechRegion
from immich_memories.speech.vad import silence_gaps


class TestSilenceGaps:
    def test_gap_between_two_regions(self):
        regions = [SpeechRegion(0.0, 1.0), SpeechRegion(3.0, 4.0)]

        gaps = silence_gaps(regions, duration=5.0)

        assert (1.0, 3.0) in gaps

    def test_leading_and_trailing_gaps_included(self):
        regions = [SpeechRegion(1.0, 2.0)]

        gaps = silence_gaps(regions, duration=4.0)

        assert (0.0, 1.0) in gaps
        assert (2.0, 4.0) in gaps

    def test_no_regions_is_one_whole_gap(self):
        gaps = silence_gaps([], duration=3.0)

        assert gaps == [(0.0, 3.0)]


class TestSileroSpeechDetector:
    def test_pure_silence_yields_no_regions(self):
        from immich_memories.speech.vad import SileroSpeechDetector

        detector = SileroSpeechDetector()
        if not detector.available:
            import pytest

            pytest.skip("silero-vad not installed")

        silence = np.zeros(16000 * 3, dtype=np.float32)

        assert detector.detect(silence, 16000) == []

    def test_unavailable_detector_returns_empty(self):
        from immich_memories.speech.vad import SileroSpeechDetector

        detector = SileroSpeechDetector()
        detector._available = False

        assert detector.detect(np.zeros(16000, dtype=np.float32), 16000) == []
