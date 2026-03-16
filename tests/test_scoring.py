"""Tests for the scoring engine (scoring.py).

Covers duration scoring, motion metrics, face detection,
MomentScore dataclass, and SceneScorer integration.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

try:
    import cv2
    import numpy as np
except ImportError:
    pytest.skip("cv2/numpy not available", allow_module_level=True)

from immich_memories.analysis.scoring import (
    MomentScore,
    SceneScorer,
    compute_duration_score,
    compute_face_score,
    compute_motion_metrics,
)

# ---------------------------------------------------------------------------
# TestComputeDurationScore — Gaussian curve
# ---------------------------------------------------------------------------


class TestComputeDurationScore:
    """Test the Gaussian-curve duration scorer."""

    def test_optimal_duration_scores_highest(self):
        # source_duration=15.0 (short, < 20) so dynamic_optimal stays at base 5.0
        score = compute_duration_score(
            duration=5.0,
            source_duration=15.0,
            optimal_duration=5.0,
            max_optimal_duration=15.0,
            target_extraction_ratio=0.25,
            min_duration=2.0,
        )
        assert score > 0.95

    def test_far_from_optimal_scores_low(self):
        score = compute_duration_score(
            duration=30.0,
            source_duration=30.0,
            optimal_duration=5.0,
            max_optimal_duration=15.0,
            target_extraction_ratio=0.25,
            min_duration=2.0,
        )
        assert score < 0.3

    def test_symmetric_around_optimal(self):
        """Score should be roughly symmetric around the optimal duration."""
        kwargs = {
            "source_duration": 60.0,
            "optimal_duration": 10.0,
            "max_optimal_duration": 15.0,
            "target_extraction_ratio": 0.15,
            "min_duration": 2.0,
        }
        score_below = compute_duration_score(duration=7.0, **kwargs)
        score_above = compute_duration_score(duration=13.0, **kwargs)
        assert abs(score_below - score_above) < 0.15

    def test_below_min_duration_returns_zero(self):
        score = compute_duration_score(
            duration=0.5,
            source_duration=30.0,
            optimal_duration=5.0,
            max_optimal_duration=15.0,
            target_extraction_ratio=0.25,
            min_duration=2.0,
        )
        assert score == pytest.approx(0.0, abs=0.1)

    def test_exactly_min_duration_low_but_nonzero(self):
        score = compute_duration_score(
            duration=2.0,
            source_duration=30.0,
            optimal_duration=5.0,
            max_optimal_duration=15.0,
            target_extraction_ratio=0.25,
            min_duration=2.0,
        )
        # At min_duration boundary, the Gaussian takes over (not the linear penalty)
        assert score > 0.0

    def test_zero_duration_returns_zero(self):
        score = compute_duration_score(
            duration=0.0,
            source_duration=30.0,
            optimal_duration=5.0,
            max_optimal_duration=15.0,
            target_extraction_ratio=0.25,
            min_duration=2.0,
        )
        assert score == 0.0

    def test_none_source_duration_uses_base_optimal(self):
        """When source_duration is None, base optimal is used."""
        score = compute_duration_score(
            duration=5.0,
            source_duration=None,
            optimal_duration=5.0,
            max_optimal_duration=15.0,
            target_extraction_ratio=0.25,
            min_duration=2.0,
        )
        assert score > 0.9


# ---------------------------------------------------------------------------
# TestComputeMotionMetrics — Motion bands
# ---------------------------------------------------------------------------


class TestComputeMotionMetrics:
    """Test optical-flow motion scoring."""

    def test_static_frames_score_low(self):
        """Two identical frames should produce low motion score."""
        frame = np.zeros((120, 160), dtype=np.uint8)
        motion_score, stability_score = compute_motion_metrics(frame, frame)
        assert motion_score < 0.4

    def test_returns_tuple_of_two_floats(self):
        frame_a = np.zeros((120, 160), dtype=np.uint8)
        frame_b = np.ones((120, 160), dtype=np.uint8) * 128
        result = compute_motion_metrics(frame_a, frame_b)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], float)
        assert isinstance(result[1], float)

    def test_high_motion_detected(self):
        """Large pixel shift should produce non-trivial motion score."""
        rng = np.random.RandomState(42)
        frame_a = rng.randint(0, 256, (120, 160), dtype=np.uint8)
        # Shift frame_b by rolling pixels (simulates horizontal motion)
        frame_b = np.roll(frame_a, shift=20, axis=1)
        motion_score, _ = compute_motion_metrics(frame_a, frame_b)
        # With detectable motion, score should leave the "too static" band
        assert motion_score >= 0.3


# ---------------------------------------------------------------------------
# TestComputeFaceScore
# ---------------------------------------------------------------------------


class TestComputeFaceScore:
    """Test face detection scoring (OpenCV path)."""

    def test_no_faces_returns_zero(self):
        """Solid black frame with no face cascade gives 0.5 (no cascade)."""
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        # With no cascade, returns 0.5 default
        score, positions = compute_face_score(
            frame, use_vision=False, vision_detector=None, face_cascade=None
        )
        assert score == 0.5
        assert positions == []

    def test_no_faces_with_cascade(self):
        """Solid black frame with a real cascade should find no faces."""
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
        score, positions = compute_face_score(
            frame, use_vision=False, vision_detector=None, face_cascade=cascade
        )
        assert score == 0.0
        assert positions == []

    def test_returns_score_and_positions(self):
        """Return type should be (float, list)."""
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        result = compute_face_score(
            frame, use_vision=False, vision_detector=None, face_cascade=None
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        score, positions = result
        assert isinstance(score, float)
        assert isinstance(positions, list)


# ---------------------------------------------------------------------------
# TestMomentScore
# ---------------------------------------------------------------------------


class TestMomentScore:
    """Test MomentScore dataclass."""

    def test_duration_property(self):
        m = MomentScore(start_time=2.0, end_time=7.0, total_score=0.8)
        assert m.duration == 5.0

    def test_midpoint_property(self):
        m = MomentScore(start_time=2.0, end_time=8.0, total_score=0.8)
        assert m.midpoint == 5.0

    def test_to_dict_contains_total_score(self):
        m = MomentScore(
            start_time=1.0,
            end_time=4.0,
            total_score=0.75,
            face_score=0.6,
            motion_score=0.8,
        )
        d = m.to_dict()
        assert "total_score" in d
        assert d["total_score"] == 0.75
        assert d["start_time"] == 1.0
        assert d["end_time"] == 4.0
        assert d["face_score"] == 0.6
        assert d["motion_score"] == 0.8

    def test_default_scores_are_zero(self):
        m = MomentScore(start_time=0.0, end_time=5.0, total_score=0.5)
        assert m.face_score == 0.0
        assert m.motion_score == 0.0
        assert m.audio_score == 0.0
        assert m.stability_score == 0.0
        assert m.content_score == 0.0
        assert m.duration_score == 0.0

    def test_to_dict_excludes_face_positions(self):
        """face_positions should not leak into the dict."""
        m = MomentScore(
            start_time=0.0,
            end_time=5.0,
            total_score=0.5,
            face_positions=[(0.5, 0.5)],
        )
        d = m.to_dict()
        assert "face_positions" not in d


# ---------------------------------------------------------------------------
# TestSceneScorerDeterministicTiebreaker
# ---------------------------------------------------------------------------


class TestSceneScorerDeterministicTiebreaker:
    """Verify _compute_sort_key is deterministic after bug fix."""

    def test_sort_key_is_deterministic(self):
        """Same inputs should always produce same sort key."""
        scorer = SceneScorer()
        moment = MomentScore(start_time=1.0, end_time=4.0, total_score=0.7)
        key_a = scorer._compute_sort_key(moment, video_duration=30.0)
        key_b = scorer._compute_sort_key(moment, video_duration=30.0)
        assert key_a == key_b

    def test_sort_key_differs_for_different_moments(self):
        scorer = SceneScorer()
        m1 = MomentScore(start_time=1.0, end_time=4.0, total_score=0.7)
        m2 = MomentScore(start_time=5.0, end_time=9.0, total_score=0.7)
        key1 = scorer._compute_sort_key(m1, video_duration=30.0)
        key2 = scorer._compute_sort_key(m2, video_duration=30.0)
        # Same total_score but different positions → different tiebreaker
        assert key1 != key2


# ---------------------------------------------------------------------------
# TestSceneScorerIntegration — requires FFmpeg
# ---------------------------------------------------------------------------


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


requires_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="FFmpeg not available")


@pytest.fixture(scope="module")
def test_clip_320(tmp_path_factory) -> Path:
    """Generate a 3s 320x240 testsrc2 clip for scoring tests."""
    out = tmp_path_factory.mktemp("scoring_fixtures") / "test_320.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-shortest",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return out


@requires_ffmpeg
@pytest.mark.integration
class TestSceneScorerIntegration:
    """Integration tests for SceneScorer with real video files."""

    def test_score_scene_returns_valid_moment(self, test_clip_320):
        """Scoring a real clip should return a valid MomentScore."""
        from immich_memories.analysis.scenes import Scene

        # content_weight=0.0 + no content_analyzer → _run_content_analysis
        # short-circuits, so no config/LLM needed
        scorer = SceneScorer(content_weight=0.0)
        scene = Scene(
            start_time=0.0,
            end_time=3.0,
            start_frame=0,
            end_frame=90,
        )
        result = scorer.score_scene(test_clip_320, scene, sample_frames=5)
        scorer.release_capture()

        assert isinstance(result, MomentScore)
        assert result.start_time == 0.0
        assert result.end_time == 3.0
        assert 0.0 <= result.total_score <= 2.0
        assert result.duration >= 0

    def test_score_produces_nonzero_motion(self, test_clip_320):
        """testsrc2 has animated counters, so motion should be detected."""
        from immich_memories.analysis.scenes import Scene

        scorer = SceneScorer(content_weight=0.0)
        scene = Scene(
            start_time=0.0,
            end_time=3.0,
            start_frame=0,
            end_frame=90,
        )
        result = scorer.score_scene(test_clip_320, scene, sample_frames=5)
        scorer.release_capture()

        assert result.motion_score > 0.0

    def test_find_best_moments_uses_cached_capture(self, test_clip_320):
        """find_best_moments → _find_best_segment should use cached VideoCapture."""
        from immich_memories.analysis.scenes import Scene

        scorer = SceneScorer(content_weight=0.0)
        # Scene longer than max_duration (2.0) triggers _find_best_segment
        scenes = [Scene(start_time=0.0, end_time=3.0, start_frame=0, end_frame=90)]
        moments = scorer.find_best_moments(
            test_clip_320,
            scenes,
            target_duration=1.5,
            min_duration=0.5,
            max_duration=2.0,
        )
        scorer.release_capture()

        assert len(moments) >= 1
        assert all(isinstance(m, MomentScore) for m in moments)
