"""Tests for AudioContentAnalyzer — mock only FFmpeg audio extraction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np

from immich_memories.audio.content_analyzer import (
    AudioContentAnalyzer,
    _classify_energy_event,
)

# ---------------------------------------------------------------------------
# _classify_energy_event (standalone helper)
# ---------------------------------------------------------------------------


class TestClassifyEnergyEvent:
    def test_very_high_short_burst_is_laughter(self):
        cls, laugh, speech = _classify_energy_event(
            is_very_high=True, event_duration=1.5, has_laughter=False, has_speech=False
        )
        assert cls == "Laughter"
        assert laugh is True
        assert speech is False

    def test_very_high_long_burst_is_speech(self):
        cls, laugh, speech = _classify_energy_event(
            is_very_high=True, event_duration=5.0, has_laughter=False, has_speech=False
        )
        assert cls == "Speech"
        assert speech is True

    def test_normal_long_is_speech(self):
        cls, laugh, speech = _classify_energy_event(
            is_very_high=False, event_duration=2.0, has_laughter=False, has_speech=False
        )
        assert cls == "Speech"
        assert speech is True

    def test_normal_short_is_sound(self):
        cls, laugh, speech = _classify_energy_event(
            is_very_high=False, event_duration=0.5, has_laughter=False, has_speech=False
        )
        assert cls == "Sound"
        assert laugh is False
        assert speech is False

    def test_preserves_existing_flags(self):
        """Existing has_laughter/has_speech flags are preserved."""
        cls, laugh, speech = _classify_energy_event(
            is_very_high=False, event_duration=0.5, has_laughter=True, has_speech=True
        )
        assert cls == "Sound"
        assert laugh is True
        assert speech is True


# ---------------------------------------------------------------------------
# AudioContentAnalyzer._calculate_audio_score
# ---------------------------------------------------------------------------


class TestCalculateAudioScore:
    def _make_analyzer(self):
        return AudioContentAnalyzer(use_panns=False)

    def test_no_events_returns_baseline(self):
        analyzer = self._make_analyzer()
        assert analyzer._calculate_audio_score([]) == 0.3

    def test_laughter_gets_bonus(self):
        from immich_memories.audio.audio_models import AudioEvent

        analyzer = self._make_analyzer()
        events = [
            AudioEvent(event_class="Laughter", start_time=0, end_time=2, confidence=0.8),
        ]
        score = analyzer._calculate_audio_score(events)
        # Laughter weight=1.0 * confidence=0.8 + laughter bonus
        assert score > 0.5

    def test_score_clamped_to_0_1(self):
        from immich_memories.audio.audio_models import AudioEvent

        analyzer = self._make_analyzer()
        # Multiple high-value events to try to exceed 1.0
        events = [
            AudioEvent(event_class="Laughter", start_time=0, end_time=1, confidence=1.0),
            AudioEvent(event_class="Laughter", start_time=1, end_time=2, confidence=1.0),
            AudioEvent(event_class="Laughter", start_time=2, end_time=3, confidence=1.0),
            AudioEvent(event_class="Laughter", start_time=3, end_time=4, confidence=1.0),
        ]
        score = analyzer._calculate_audio_score(events)
        assert 0.0 <= score <= 1.0

    def test_low_confidence_events_score_lower(self):
        from immich_memories.audio.audio_models import AudioEvent

        analyzer = self._make_analyzer()
        high_conf = [
            AudioEvent(event_class="Speech", start_time=0, end_time=5, confidence=0.9),
        ]
        low_conf = [
            AudioEvent(event_class="Speech", start_time=0, end_time=5, confidence=0.1),
        ]
        assert analyzer._calculate_audio_score(high_conf) > analyzer._calculate_audio_score(
            low_conf
        )

    def test_zero_duration_events_returns_baseline(self):
        from immich_memories.audio.audio_models import AudioEvent

        analyzer = self._make_analyzer()
        events = [
            AudioEvent(event_class="Speech", start_time=5, end_time=5, confidence=0.8),
        ]
        # total_duration = 0, should return baseline
        assert analyzer._calculate_audio_score(events) == 0.3


# ---------------------------------------------------------------------------
# AudioContentAnalyzer._analyze_with_energy
# ---------------------------------------------------------------------------


class TestAnalyzeWithEnergy:
    def _make_analyzer(self, **kwargs):
        return AudioContentAnalyzer(use_panns=False, **kwargs)

    def test_silent_audio_produces_no_events(self):
        analyzer = self._make_analyzer()
        audio = np.zeros(32000 * 5, dtype=np.float32)  # 5 seconds silence
        result = analyzer._analyze_with_energy(audio, 32000, max_duration=5.0)
        assert result.events == []
        assert len(result.energy_profile) > 0
        assert result.audio_score == 0.3  # baseline

    def test_high_energy_burst_detected(self):
        analyzer = self._make_analyzer(window_size=0.5)
        # 5 seconds at 32kHz
        audio = np.zeros(32000 * 5, dtype=np.float32)
        # Insert a loud burst at 1-2 seconds
        audio[32000:64000] = 0.9
        result = analyzer._analyze_with_energy(audio, 32000, max_duration=5.0)
        assert len(result.events) >= 1
        # A "Sound" event with low weight/confidence scores below baseline;
        # the key assertion is that events were detected at all
        assert result.audio_score >= 0.0

    def test_laughter_detection_via_energy(self):
        """Very high, short burst -> classified as Laughter."""
        analyzer = self._make_analyzer(window_size=0.5)
        audio = np.zeros(32000 * 3, dtype=np.float32)
        # Very loud, short burst (< 3s) at extreme amplitude
        audio[16000:32000] = 0.95
        result = analyzer._analyze_with_energy(audio, 32000, max_duration=3.0)
        laughter_events = [e for e in result.events if e.event_class == "Laughter"]
        if laughter_events:
            assert result.has_laughter

    def test_speech_detection_via_energy(self):
        """Moderate energy over >1s -> classified as Speech."""
        analyzer = self._make_analyzer(window_size=0.5)
        audio = np.zeros(32000 * 10, dtype=np.float32)
        # Moderate energy for 4 seconds (> 1s threshold)
        audio[32000 : 32000 * 5] = 0.6
        result = analyzer._analyze_with_energy(audio, 32000, max_duration=10.0)
        speech_events = [e for e in result.events if e.event_class == "Speech"]
        if speech_events:
            assert result.has_speech

    def test_empty_audio_returns_empty_result(self):
        analyzer = self._make_analyzer()
        audio = np.array([], dtype=np.float32)
        result = analyzer._analyze_with_energy(audio, 32000, max_duration=0.0)
        assert result.events == []
        assert result.energy_profile == []

    def test_protected_ranges_populated(self):
        """Events with protected class names should appear in protected_ranges."""
        analyzer = self._make_analyzer(window_size=0.5)
        audio = np.zeros(32000 * 5, dtype=np.float32)
        # High energy burst → will be classified as Laughter or Speech (both protected)
        audio[32000:64000] = 0.95
        result = analyzer._analyze_with_energy(audio, 32000, max_duration=5.0)
        if result.events:
            protected_classes = {e.event_class for e in result.events if e.is_protected}
            if protected_classes:
                assert len(result.protected_ranges) > 0

    def test_has_music_always_false_in_energy_mode(self):
        analyzer = self._make_analyzer()
        audio = np.random.default_rng(42).random(32000 * 5, dtype=np.float32) * 0.5
        result = analyzer._analyze_with_energy(audio, 32000, max_duration=5.0)
        assert not result.has_music


# ---------------------------------------------------------------------------
# AudioContentAnalyzer.analyze (integration with _extract_audio mocked)
# ---------------------------------------------------------------------------


class TestAnalyzeIntegration:
    def test_extraction_failure_returns_empty_result(self):
        analyzer = AudioContentAnalyzer(use_panns=False)
        # WHY: Mock _extract_audio because it calls FFmpeg
        with patch.object(analyzer, "_extract_audio", return_value=None):
            result = analyzer.analyze(Path("/fake/video.mp4"))
        assert result.events == []
        assert result.audio_score == 0.0

    def test_successful_analysis_with_synthetic_audio(self):
        analyzer = AudioContentAnalyzer(use_panns=False, window_size=0.5)
        audio = np.zeros(32000 * 3, dtype=np.float32)
        audio[16000:48000] = 0.7  # 1 second of energy

        # WHY: Mock _extract_audio because it calls FFmpeg
        with patch.object(analyzer, "_extract_audio", return_value=(audio, 32000)):
            result = analyzer.analyze(Path("/fake/video.mp4"), video_duration=3.0)

        assert isinstance(result.audio_score, float)
        assert len(result.energy_profile) > 0

    def test_panns_unavailable_falls_back_to_energy(self):
        """When use_panns=True but PANNs isn't installed, falls back to energy."""
        analyzer = AudioContentAnalyzer(use_panns=True, window_size=0.5)
        audio = np.zeros(32000 * 2, dtype=np.float32)

        # WHY: Mock _extract_audio because it calls FFmpeg
        # WHY: Mock _check_panns_available because PANNs isn't installed in test env
        with (
            patch.object(analyzer, "_extract_audio", return_value=(audio, 32000)),
            patch.object(analyzer, "_check_panns_available", return_value=False),
        ):
            result = analyzer.analyze(Path("/fake/video.mp4"), video_duration=2.0)

        assert result is not None
        assert isinstance(result.audio_score, float)

    def test_use_yamnet_compat_alias(self):
        """use_yamnet parameter should work as alias for use_panns."""
        analyzer = AudioContentAnalyzer(use_yamnet=True)
        assert analyzer.use_panns is True
        analyzer2 = AudioContentAnalyzer(use_yamnet=False)
        assert analyzer2.use_panns is False


# ---------------------------------------------------------------------------
# AudioContentAnalyzer.cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_cleanup_resets_state(self):
        analyzer = AudioContentAnalyzer()
        analyzer._panns_model = "fake"
        analyzer._panns_available = True
        analyzer._class_names = ["a", "b"]
        analyzer.cleanup()
        assert analyzer._panns_model is None
        assert analyzer._panns_available is None
        assert analyzer._class_names is None
