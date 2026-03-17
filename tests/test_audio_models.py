"""Tests for audio data models — pure logic, no external deps."""

from __future__ import annotations

import pytest

from immich_memories.audio.audio_models import (
    AUDIO_EVENT_WEIGHTS,
    PROTECTED_EVENTS,
    AudioAnalysisResult,
    AudioEvent,
    adjust_boundaries_for_audio,
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


class TestSafeCutPoints:
    def test_no_protected_ranges_returns_empty(self):
        result = AudioAnalysisResult()
        assert result.get_safe_cut_points() == []

    def test_single_protected_range(self):
        result = AudioAnalysisResult(protected_ranges=[(2.0, 4.0)])
        points = result.get_safe_cut_points(min_gap=0.3)
        # Should have point before 2.0 and after 4.0
        assert any(p < 2.0 for p in points)
        assert any(p > 4.0 for p in points)

    def test_multiple_protected_ranges(self):
        result = AudioAnalysisResult(protected_ranges=[(1.0, 2.0), (5.0, 7.0)])
        points = result.get_safe_cut_points(min_gap=0.3)
        assert len(points) >= 2

    def test_min_gap_respected(self):
        """If ranges are too close, some cut points are skipped."""
        result = AudioAnalysisResult(protected_ranges=[(1.0, 1.1), (1.2, 1.3)])
        points = result.get_safe_cut_points(min_gap=0.5)
        # With min_gap 0.5, the gap between end of first (1.1) and start of second (1.2)
        # is only 0.1 — not enough for an additional cut point
        # But there should still be at least 1 point
        assert len(points) >= 1

    def test_overlapping_protected_ranges_handled(self):
        result = AudioAnalysisResult(protected_ranges=[(1.0, 3.0), (2.0, 4.0)])
        points = result.get_safe_cut_points()
        # Should not crash; output should be valid
        assert isinstance(points, list)


# ---------------------------------------------------------------------------
# adjust_boundaries_for_audio
# ---------------------------------------------------------------------------


class TestAdjustBoundaries:
    def test_no_protected_ranges_no_change(self):
        result = AudioAnalysisResult()
        start, end = adjust_boundaries_for_audio(1.0, 5.0, result)
        assert start == 1.0
        assert end == 5.0

    def test_start_inside_protected_range_moves_after(self):
        result = AudioAnalysisResult(protected_ranges=[(0.5, 1.3)])
        start, end = adjust_boundaries_for_audio(1.0, 5.0, result, max_adjustment=0.5)
        # Start (1.0) is inside [0.5, 1.3], should move to 1.4
        assert start > 1.0

    def test_end_inside_protected_range_moves_after(self):
        result = AudioAnalysisResult(protected_ranges=[(4.8, 5.2)])
        start, end = adjust_boundaries_for_audio(1.0, 5.0, result, max_adjustment=0.5)
        # End (5.0) is inside [4.8, 5.2], should adjust
        assert end != 5.0

    def test_start_moves_before_protected_when_closer(self):
        result = AudioAnalysisResult(protected_ranges=[(0.9, 5.0)])
        start, end = adjust_boundaries_for_audio(1.0, 5.5, result, max_adjustment=0.5)
        # Start (1.0) is inside [0.9, 5.0]. Moving after (5.0) exceeds max_adjustment.
        # Moving before (0.9) is within 0.5 adjustment.
        assert start < 1.0

    def test_invalid_range_reverts(self):
        """If adjustment makes end <= start, revert to original."""
        result = AudioAnalysisResult(protected_ranges=[(0.5, 3.0)])
        # Start 1.0, end 1.5 — both inside range. Adjusting start to 3.1 would exceed end.
        start, end = adjust_boundaries_for_audio(1.0, 1.5, result, max_adjustment=0.5)
        # Since adjustment of start forward (to 3.1) exceeds max_adjustment (0.5),
        # and backward (to 0.4) is within limit, start moves to 0.4
        # But end 1.5 inside [0.5, 3.0] — moving forward to 3.1 exceeds max_adjustment
        # moving backward to 0.4 would make end < start — so should revert
        assert end >= start

    def test_start_never_negative(self):
        result = AudioAnalysisResult(protected_ranges=[(0.0, 0.5)])
        start, end = adjust_boundaries_for_audio(0.2, 5.0, result, max_adjustment=0.5)
        assert start >= 0


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
