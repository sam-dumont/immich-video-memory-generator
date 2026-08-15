"""Tests for utterance-completion scoring. Windowing is pure; the model is optional."""

from __future__ import annotations

import numpy as np

from immich_memories.speech.turn_detection import window_ending_at


class TestWindowEndingAt:
    def test_window_ends_at_requested_time(self):
        audio = np.arange(16000 * 10, dtype=np.float32)

        window = window_ending_at(audio, 16000, end_time=5.0, window_s=2.0)

        assert len(window) == 16000 * 2
        assert window[-1] == audio[16000 * 5 - 1]

    def test_short_audio_is_left_padded(self):
        audio = np.ones(16000, dtype=np.float32)

        window = window_ending_at(audio, 16000, end_time=1.0, window_s=8.0)

        assert len(window) == 16000 * 8
        assert window[0] == 0.0
        assert window[-1] == 1.0

    def test_end_time_past_audio_clamps(self):
        audio = np.ones(16000 * 2, dtype=np.float32)

        window = window_ending_at(audio, 16000, end_time=99.0, window_s=1.0)

        assert len(window) == 16000


class TestSmartTurnDetector:
    def test_unavailable_detector_returns_neutral(self):
        from immich_memories.speech.turn_detection import SmartTurnDetector

        detector = SmartTurnDetector()
        detector._available = False

        assert detector.completion_probability(np.zeros(16000, dtype=np.float32), 16000) == 0.5
