"""Unit tests for silence detection and segment boundary adjustment.

Tests the pure-function core of silence_detection.py using synthetic
numpy audio arrays — no FFmpeg, no video files needed.
"""

from __future__ import annotations

import numpy as np
import pytest

from immich_memories.analysis.silence_detection import (
    _analyze_audio_for_silence,
    _collect_silence_gaps,
    adjust_segment_to_silence,
    find_nearest_silence,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_audio(
    sample_rate: int,
    duration: float,
    segments: list[tuple[float, float, float]],
) -> np.ndarray:
    """Create synthetic audio with known noise/silence segments.

    Args:
        sample_rate: Sample rate in Hz.
        duration: Total duration in seconds.
        segments: List of (start, end, amplitude) tuples.
            amplitude=0.0 for silence, >0 for noise.
            Unspecified regions default to silence.
    """
    samples = int(sample_rate * duration)
    audio = np.zeros(samples, dtype=np.float32)
    rng = np.random.RandomState(42)
    for seg_start, seg_end, amplitude in segments:
        s = int(seg_start * sample_rate)
        e = int(seg_end * sample_rate)
        audio[s:e] = rng.uniform(-amplitude, amplitude, e - s).astype(np.float32)
    return audio


def _window_params(sample_rate: int, duration: float, window_size: float) -> tuple[int, int, float]:
    """Return (samples_per_window, num_windows, total_duration)."""
    total_samples = int(sample_rate * duration)
    samples_per_window = int(sample_rate * window_size)
    num_windows = total_samples // samples_per_window
    total_duration = total_samples / sample_rate
    return samples_per_window, num_windows, total_duration


# ---------------------------------------------------------------------------
# TestCollectSilenceGaps
# ---------------------------------------------------------------------------


class TestCollectSilenceGaps:
    """Tests for _collect_silence_gaps — the low-level window walker."""

    RATE = 16000
    WINDOW = 0.1
    THRESHOLD = -30.0
    MIN_SILENCE = 0.3

    def test_all_silence_returns_one_gap(self) -> None:
        """Full silence → single gap spanning the entire duration."""
        duration = 2.0
        audio = _make_audio(self.RATE, duration, [])
        spw, nw, td = _window_params(self.RATE, duration, self.WINDOW)

        gaps = _collect_silence_gaps(
            audio, spw, nw, self.THRESHOLD, self.MIN_SILENCE, self.WINDOW, td
        )

        assert len(gaps) == 1, "All-silence audio should produce exactly one gap"
        assert gaps[0][0] == pytest.approx(0.0, abs=0.15), (
            "Gap should start at the beginning of the audio"
        )
        assert gaps[0][1] == pytest.approx(td, abs=0.15), "Gap should end at the total duration"

    def test_all_noise_returns_no_gaps(self) -> None:
        """Loud noise across entire file → no silence gaps detected."""
        duration = 2.0
        audio = _make_audio(self.RATE, duration, [(0.0, 2.0, 0.5)])
        spw, nw, td = _window_params(self.RATE, duration, self.WINDOW)

        gaps = _collect_silence_gaps(
            audio, spw, nw, self.THRESHOLD, self.MIN_SILENCE, self.WINDOW, td
        )

        assert gaps == [], "All-noise audio should produce no silence gaps"

    def test_noise_silence_noise_detects_middle_gap(self) -> None:
        """1s noise, 1s silence, 1s noise → one gap in the middle."""
        duration = 3.0
        audio = _make_audio(self.RATE, duration, [(0.0, 1.0, 0.5), (2.0, 3.0, 0.5)])
        spw, nw, td = _window_params(self.RATE, duration, self.WINDOW)

        gaps = _collect_silence_gaps(
            audio, spw, nw, self.THRESHOLD, self.MIN_SILENCE, self.WINDOW, td
        )

        assert len(gaps) == 1, "Should detect exactly one silence gap in the middle"
        assert gaps[0][0] == pytest.approx(1.0, abs=0.15), (
            "Gap should start around 1.0s where noise ends"
        )
        assert gaps[0][1] == pytest.approx(2.0, abs=0.15), (
            "Gap should end around 2.0s where noise resumes"
        )

    def test_short_silence_below_min_duration_is_ignored(self) -> None:
        """A 0.2s silence gap (below min_silence_duration=0.3) is filtered out."""
        duration = 3.0
        # noise, 0.2s silence, noise
        audio = _make_audio(self.RATE, duration, [(0.0, 1.0, 0.5), (1.2, 3.0, 0.5)])
        spw, nw, td = _window_params(self.RATE, duration, self.WINDOW)

        gaps = _collect_silence_gaps(
            audio, spw, nw, self.THRESHOLD, self.MIN_SILENCE, self.WINDOW, td
        )

        assert gaps == [], "Silence shorter than min_silence_duration should be ignored"

    def test_multiple_silence_gaps_all_detected(self) -> None:
        """Two distinct silence regions → two gaps returned."""
        duration = 5.0
        # noise 0-1, silence 1-2, noise 2-3, silence 3-4, noise 4-5
        audio = _make_audio(
            self.RATE,
            duration,
            [(0.0, 1.0, 0.5), (2.0, 3.0, 0.5), (4.0, 5.0, 0.5)],
        )
        spw, nw, td = _window_params(self.RATE, duration, self.WINDOW)

        gaps = _collect_silence_gaps(
            audio, spw, nw, self.THRESHOLD, self.MIN_SILENCE, self.WINDOW, td
        )

        assert len(gaps) == 2, "Should detect two distinct silence gaps"
        assert gaps[0][0] == pytest.approx(1.0, abs=0.15), "First gap should start around 1.0s"
        assert gaps[0][1] == pytest.approx(2.0, abs=0.15), "First gap should end around 2.0s"
        assert gaps[1][0] == pytest.approx(3.0, abs=0.15), "Second gap should start around 3.0s"
        assert gaps[1][1] == pytest.approx(4.0, abs=0.15), "Second gap should end around 4.0s"

    def test_trailing_silence_included(self) -> None:
        """Noise followed by trailing silence → gap extends to end."""
        duration = 3.0
        audio = _make_audio(self.RATE, duration, [(0.0, 1.0, 0.5)])
        spw, nw, td = _window_params(self.RATE, duration, self.WINDOW)

        gaps = _collect_silence_gaps(
            audio, spw, nw, self.THRESHOLD, self.MIN_SILENCE, self.WINDOW, td
        )

        assert len(gaps) == 1, "Trailing silence should produce one gap"
        assert gaps[0][0] == pytest.approx(1.0, abs=0.15), (
            "Trailing gap should start where noise ends"
        )
        assert gaps[0][1] == pytest.approx(td, abs=0.15), (
            "Trailing gap should extend to total_duration"
        )

    def test_threshold_sensitivity(self) -> None:
        """Same audio detected as silence or noise depending on threshold.

        With very quiet audio (amplitude=0.005), a strict threshold (-60 dB)
        sees it as noise; a lenient threshold (-30 dB) sees it as silence.
        """
        duration = 2.0
        quiet_amplitude = 0.005
        audio = _make_audio(self.RATE, duration, [(0.0, 2.0, quiet_amplitude)])
        spw, nw, td = _window_params(self.RATE, duration, self.WINDOW)

        strict_gaps = _collect_silence_gaps(
            audio, spw, nw, -60.0, self.MIN_SILENCE, self.WINDOW, td
        )
        lenient_gaps = _collect_silence_gaps(
            audio, spw, nw, -30.0, self.MIN_SILENCE, self.WINDOW, td
        )

        assert strict_gaps == [], "Strict threshold should not detect quiet audio as silence"
        assert len(lenient_gaps) == 1, "Lenient threshold should detect quiet audio as silence"

    def test_leading_silence_detected(self) -> None:
        """Silence at the start followed by noise → gap at the beginning."""
        duration = 3.0
        audio = _make_audio(self.RATE, duration, [(1.5, 3.0, 0.5)])
        spw, nw, td = _window_params(self.RATE, duration, self.WINDOW)

        gaps = _collect_silence_gaps(
            audio, spw, nw, self.THRESHOLD, self.MIN_SILENCE, self.WINDOW, td
        )

        assert len(gaps) == 1, "Leading silence should produce one gap"
        assert gaps[0][0] == pytest.approx(0.0, abs=0.15), "Leading gap should start at 0.0s"
        assert gaps[0][1] == pytest.approx(1.5, abs=0.15), (
            "Leading gap should end where noise starts"
        )


# ---------------------------------------------------------------------------
# TestAnalyzeAudioForSilence
# ---------------------------------------------------------------------------


class TestAnalyzeAudioForSilence:
    """Tests for _analyze_audio_for_silence — the wrapper that computes window params."""

    RATE = 16000

    def test_known_pattern_noise_silence_noise(self) -> None:
        """1s noise, 0.5s silence, 1s noise → detects the gap."""
        duration = 2.5
        audio = _make_audio(self.RATE, duration, [(0.0, 1.0, 0.5), (1.5, 2.5, 0.5)])

        gaps = _analyze_audio_for_silence(
            audio, self.RATE, threshold_db=-30.0, min_silence_duration=0.3, window_size=0.1
        )

        assert len(gaps) == 1, "Should detect one silence gap between noise regions"
        assert gaps[0][0] == pytest.approx(1.0, abs=0.15), "Gap should start where first noise ends"
        assert gaps[0][1] == pytest.approx(1.5, abs=0.15), (
            "Gap should end where second noise starts"
        )

    def test_different_sample_rate_same_result(self) -> None:
        """Same audio pattern at 8kHz and 44.1kHz should find the same gap."""
        for rate in (8000, 44100):
            duration = 3.0
            audio = _make_audio(rate, duration, [(0.0, 1.0, 0.5), (2.0, 3.0, 0.5)])

            gaps = _analyze_audio_for_silence(
                audio, rate, threshold_db=-30.0, min_silence_duration=0.3, window_size=0.1
            )

            assert len(gaps) == 1, f"At {rate}Hz, should detect one gap in the middle"
            assert gaps[0][0] == pytest.approx(1.0, abs=0.15), (
                f"At {rate}Hz, gap start should be ~1.0s"
            )
            assert gaps[0][1] == pytest.approx(2.0, abs=0.15), (
                f"At {rate}Hz, gap end should be ~2.0s"
            )

    def test_empty_audio_all_silence(self) -> None:
        """Zero-amplitude audio → single gap spanning the whole duration."""
        duration = 1.0
        audio = np.zeros(int(self.RATE * duration), dtype=np.float32)

        gaps = _analyze_audio_for_silence(
            audio, self.RATE, threshold_db=-30.0, min_silence_duration=0.3, window_size=0.1
        )

        assert len(gaps) == 1, "Pure silence should produce exactly one gap"

    def test_window_size_affects_resolution(self) -> None:
        """Larger window size should still detect a sufficiently long gap."""
        duration = 4.0
        # noise, 1s silence, noise
        audio = _make_audio(self.RATE, duration, [(0.0, 1.5, 0.5), (2.5, 4.0, 0.5)])

        gaps_fine = _analyze_audio_for_silence(
            audio, self.RATE, threshold_db=-30.0, min_silence_duration=0.3, window_size=0.05
        )
        gaps_coarse = _analyze_audio_for_silence(
            audio, self.RATE, threshold_db=-30.0, min_silence_duration=0.3, window_size=0.5
        )

        assert len(gaps_fine) == 1, "Fine window should detect the gap"
        assert len(gaps_coarse) == 1, "Coarse window should also detect the gap"
        # Coarse window has less temporal precision
        assert gaps_fine[0][0] == pytest.approx(1.5, abs=0.1), (
            "Fine window gap start should be precise"
        )
        assert gaps_coarse[0][0] == pytest.approx(1.5, abs=0.6), (
            "Coarse window gap start has lower precision"
        )


# ---------------------------------------------------------------------------
# TestFindNearestSilence
# ---------------------------------------------------------------------------


class TestFindNearestSilence:
    """Tests for find_nearest_silence — snaps a time position to the nearest gap boundary."""

    def test_empty_gaps_returns_none(self) -> None:
        """No silence gaps → returns None."""
        result = find_nearest_silence(5.0, [])
        assert result is None, "Empty gap list should return None"

    def test_position_at_gap_start(self) -> None:
        """Position exactly at gap start → returns that start."""
        gaps = [(2.0, 3.0)]
        result = find_nearest_silence(2.0, gaps, max_adjustment=1.0)
        assert result == pytest.approx(2.0), "Position at gap start should snap to gap start"

    def test_position_near_gap_end(self) -> None:
        """Position close to gap end → returns gap end."""
        gaps = [(2.0, 3.0)]
        result = find_nearest_silence(3.2, gaps, max_adjustment=1.0)
        assert result == pytest.approx(3.0), "Position near gap end should snap to gap end"

    def test_position_between_two_gaps_returns_closest(self) -> None:
        """Position between two gaps → returns the closer boundary."""
        gaps = [(1.0, 2.0), (4.0, 5.0)]
        # 2.5 is 0.5 from gap[0] end, 1.5 from gap[1] start
        result = find_nearest_silence(2.5, gaps, max_adjustment=2.0)
        assert result == pytest.approx(2.0), "Should snap to the closer gap boundary"

    def test_position_beyond_max_adjustment_returns_none(self) -> None:
        """All gaps too far away → returns None."""
        gaps = [(10.0, 11.0)]
        result = find_nearest_silence(1.0, gaps, max_adjustment=1.0)
        assert result is None, "Gap beyond max_adjustment should not be returned"

    def test_long_gap_midpoint_is_candidate(self) -> None:
        """For gaps > 0.5s, the midpoint is also a snap candidate."""
        gaps = [(2.0, 4.0)]  # 2s gap, midpoint = 3.0
        # Position at 3.1 — closest to midpoint 3.0
        result = find_nearest_silence(3.1, gaps, max_adjustment=1.0)
        assert result == pytest.approx(3.0), "Long gap midpoint should be considered as a candidate"

    def test_short_gap_midpoint_not_used(self) -> None:
        """For gaps <= 0.5s, only start and end are candidates."""
        gaps = [(2.0, 2.4)]  # 0.4s gap, midpoint = 2.2
        # Position at 2.2 — equidistant from start and end (0.2)
        result = find_nearest_silence(2.2, gaps, max_adjustment=1.0)
        # Should snap to start (2.0) since start is checked first and ties go to first
        assert result in (2.0, 2.4), "Short gap should only snap to start or end, not midpoint"

    def test_multiple_gaps_finds_nearest(self) -> None:
        """With many gaps, returns the absolute closest boundary."""
        gaps = [(1.0, 1.5), (3.0, 3.5), (6.0, 6.5)]
        # Position at 3.3 — closest to gap[1] end (3.5, dist=0.2)
        result = find_nearest_silence(3.3, gaps, max_adjustment=1.0)
        assert result == pytest.approx(3.5), "Should find the nearest boundary across all gaps"


# ---------------------------------------------------------------------------
# TestAdjustSegmentToSilence
# ---------------------------------------------------------------------------


class TestAdjustSegmentToSilence:
    """Tests for adjust_segment_to_silence — snaps both boundaries while maintaining min duration."""

    def test_no_gaps_returns_original(self) -> None:
        """No silence gaps → original boundaries returned unchanged."""
        result = adjust_segment_to_silence(1.0, 5.0, [])
        assert result == (1.0, 5.0), "Without gaps, boundaries should be unchanged"

    def test_start_snaps_to_gap(self) -> None:
        """Start near a silence gap → snaps start to gap boundary."""
        gaps = [(0.8, 1.2)]
        start, end = adjust_segment_to_silence(1.0, 5.0, gaps, max_adjustment=1.0, min_duration=2.0)
        # Start should snap to 0.8 or 1.2 (whichever is closer)
        assert start == pytest.approx(1.0, abs=0.25), "Start should snap to nearest gap boundary"
        assert end == pytest.approx(5.0, abs=0.1), "End should remain unchanged when no nearby gap"

    def test_end_snaps_to_gap(self) -> None:
        """End near a silence gap → snaps end to gap boundary."""
        gaps = [(4.8, 5.3)]
        start, end = adjust_segment_to_silence(1.0, 5.0, gaps, max_adjustment=1.0, min_duration=2.0)
        assert start == pytest.approx(1.0, abs=0.1), (
            "Start should remain unchanged when no nearby gap"
        )
        assert end in (pytest.approx(4.8, abs=0.01), pytest.approx(5.3, abs=0.01)), (
            "End should snap to nearest gap boundary"
        )

    def test_both_boundaries_adjusted(self) -> None:
        """Both start and end near gaps → both snap, duration maintained."""
        gaps = [(0.5, 1.2), (4.8, 5.5)]
        start, end = adjust_segment_to_silence(1.0, 5.0, gaps, max_adjustment=1.0, min_duration=2.0)
        # Both should be adjusted
        duration = end - start
        assert duration >= 2.0, (
            f"Adjusted segment duration {duration}s must be >= min_duration 2.0s"
        )

    def test_short_result_extended_to_min_duration(self) -> None:
        """When adjustment shrinks segment below min_duration, it gets extended."""
        # Gaps that would push start forward and end backward, making segment tiny
        gaps = [(1.0, 1.8), (2.2, 3.0)]
        start, end = adjust_segment_to_silence(1.1, 2.9, gaps, max_adjustment=1.0, min_duration=3.0)
        duration = end - start
        assert duration >= 3.0, (
            f"Segment duration {duration}s must be extended to >= min_duration 3.0s"
        )

    def test_boundaries_far_from_gaps_unchanged(self) -> None:
        """Segment far from all gaps → boundaries unchanged."""
        gaps = [(10.0, 11.0)]
        start, end = adjust_segment_to_silence(1.0, 5.0, gaps, max_adjustment=1.0, min_duration=2.0)
        assert (start, end) == (1.0, 5.0), "Boundaries far from gaps should remain unchanged"

    def test_extension_uses_original_duration_when_possible(self) -> None:
        """When original duration >= min_duration, extension restores original length."""
        # Original: 1.0 to 5.0 (4s). Gaps that snap start to 2.5, end to 3.0 (0.5s).
        # Since original >= min_duration, should restore to 4s centered on midpoint.
        gaps = [(2.4, 2.6), (2.9, 3.1)]
        start, end = adjust_segment_to_silence(2.5, 3.0, gaps, max_adjustment=1.0, min_duration=2.0)
        duration = end - start
        assert duration >= 2.0, f"Extended duration {duration}s should be at least min_duration"
