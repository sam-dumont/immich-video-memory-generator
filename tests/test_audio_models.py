"""Tests for audio data models — pure logic, no external deps."""

from __future__ import annotations

import pytest

from immich_memories.audio.audio_models import (
    AUDIO_EVENT_WEIGHTS,
    PROTECTED_EVENTS,
    AudioAnalysisResult,
    AudioEvent,
    classify_audio_event,
)

# ---------------------------------------------------------------------------
# AudioEvent
# ---------------------------------------------------------------------------


class TestAudioEvent:
    def test_duration(self):
        event = AudioEvent(event_class="Laughter", start_time=1.0, end_time=3.5, confidence=0.8)
        assert event.duration == pytest.approx(2.5)

    def test_weight_known_event(self):
        event = AudioEvent(event_class="Laughter", start_time=0, end_time=1, confidence=0.9)
        assert event.weight == 1.0

    def test_weight_unknown_event_fallback(self):
        event = AudioEvent(event_class="UnknownSound", start_time=0, end_time=1, confidence=0.5)
        assert event.weight == 0.2

    def test_is_protected(self):
        for event_class in PROTECTED_EVENTS:
            e = AudioEvent(event_class=event_class, start_time=0, end_time=1, confidence=0.5)
            assert e.is_protected, f"{event_class} should be protected"

    def test_not_protected(self):
        e = AudioEvent(event_class="Bird", start_time=0, end_time=1, confidence=0.5)
        assert not e.is_protected

    def test_zero_duration_event(self):
        e = AudioEvent(event_class="Speech", start_time=5.0, end_time=5.0, confidence=0.4)
        assert e.duration == 0.0


# ---------------------------------------------------------------------------
# AudioAnalysisResult.get_safe_cut_points
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# adjust_boundaries_for_audio
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# classify_audio_event
# ---------------------------------------------------------------------------


class TestClassifyAudioEvent:
    @pytest.mark.parametrize(
        "class_name,expected",
        [
            ("Laughter", "laughter"),
            ("Baby laughter", "baby"),
            ("Speech", "speech"),
            ("Singing", "singing"),
            ("Cheering", "crowd"),
            ("Engine", "engine"),
            ("Guitar", "music"),
            ("Bird", "nature"),
            ("Dog", "animals"),
        ],
    )
    def test_known_categories(self, class_name, expected):
        assert classify_audio_event(class_name) == expected

    def test_case_insensitive(self):
        assert classify_audio_event("LAUGHTER") == "laughter"
        assert classify_audio_event("singing") == "singing"

    def test_unknown_returns_none(self):
        assert classify_audio_event("SomeUnknownClass") is None
        assert classify_audio_event("Silence") is None

    def test_baby_takes_priority_over_laughter(self):
        """'Baby laughter' should match 'baby', not 'laughter' — first match wins."""
        assert classify_audio_event("Baby laughter") == "baby"


# ---------------------------------------------------------------------------
# AUDIO_EVENT_WEIGHTS consistency
# ---------------------------------------------------------------------------


class TestAudioEventWeights:
    def test_all_weights_between_0_and_1(self):
        for event, weight in AUDIO_EVENT_WEIGHTS.items():
            assert 0.0 <= weight <= 1.0, f"{event} has invalid weight {weight}"

    def test_laughter_is_highest(self):
        assert AUDIO_EVENT_WEIGHTS["Laughter"] == 1.0

    def test_silence_is_zero(self):
        assert AUDIO_EVENT_WEIGHTS["Silence"] == 0.0


# ---------------------------------------------------------------------------
# AudioAnalysisResult default state
# ---------------------------------------------------------------------------


class TestAudioAnalysisResultDefaults:
    def test_default_empty(self):
        r = AudioAnalysisResult()
        assert r.events == []
        assert r.audio_score == 0.0
        assert not r.has_laughter
        assert not r.has_speech
        assert not r.has_music
        assert r.detected_categories == set()
        assert r.energy_profile == []
        assert r.protected_ranges == []
