"""Tests for the scoring module."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from immich_memories.analysis.scenes import Scene
from immich_memories.analysis.scoring import MomentScore, SceneScorer


class TestMomentScore:
    """Tests for MomentScore dataclass."""

    def test_duration(self):
        """Should calculate duration correctly."""
        score = MomentScore(
            start_time=5.0,
            end_time=10.0,
            total_score=0.7,
        )
        assert score.duration == 5.0

    def test_midpoint(self):
        """Should calculate midpoint correctly."""
        score = MomentScore(
            start_time=5.0,
            end_time=10.0,
            total_score=0.7,
        )
        assert score.midpoint == 7.5

    def test_to_dict(self):
        """Should convert to dictionary."""
        score = MomentScore(
            start_time=5.0,
            end_time=10.0,
            total_score=0.7,
            face_score=0.8,
            motion_score=0.6,
            audio_score=0.5,
            stability_score=0.7,
        )
        d = score.to_dict()

        assert d["start_time"] == 5.0
        assert d["end_time"] == 10.0
        assert d["total_score"] == 0.7
        assert d["face_score"] == 0.8


class TestSceneScorer:
    """Tests for SceneScorer class."""

    @pytest.fixture
    def scorer(self):
        """Create a SceneScorer instance."""
        return SceneScorer()

    def test_generate_segments_short_video(self, scorer):
        """Short video should return single segment."""
        with patch("immich_memories.analysis.scoring.get_video_info") as mock_info:
            mock_info.return_value = {"duration": 2.0, "fps": 30}

            segments = scorer._generate_segments(Path("test.mp4"), 3.0, 0.5)

            assert len(segments) == 1
            assert segments[0].start_time == 0
            assert segments[0].end_time == 2.0

    def test_generate_segments_normal_video(self, scorer):
        """Normal video should generate overlapping segments."""
        with patch("immich_memories.analysis.scoring.get_video_info") as mock_info:
            mock_info.return_value = {"duration": 10.0, "fps": 30}

            # 3 second segments with 50% overlap = step of 1.5s
            # At 10s duration: segments at 0-3, 1.5-4.5, 3-6, 4.5-7.5, 6-9, 7.5-10
            segments = scorer._generate_segments(Path("test.mp4"), 3.0, 0.5)

            assert len(segments) >= 4
            assert segments[0].start_time == 0
            assert segments[0].end_time == 3.0
            # Check overlap
            assert segments[1].start_time == 1.5
            assert segments[1].end_time == 4.5

    def test_generate_segments_zero_duration(self, scorer):
        """Zero duration video should return empty list."""
        with patch("immich_memories.analysis.scoring.get_video_info") as mock_info:
            mock_info.return_value = {"duration": 0, "fps": 30}

            segments = scorer._generate_segments(Path("test.mp4"), 3.0, 0.5)

            assert len(segments) == 0

    def test_compute_sort_key_prefers_higher_score(self, scorer):
        """Higher score should sort first."""
        moment1 = MomentScore(
            start_time=0,
            end_time=5,
            total_score=0.8,
            face_score=0.8,
            motion_score=0.8,
            stability_score=0.8,
        )
        moment2 = MomentScore(
            start_time=5,
            end_time=10,
            total_score=0.6,
            face_score=0.6,
            motion_score=0.6,
            stability_score=0.6,
        )

        key1 = scorer._compute_sort_key(moment1, 30.0)
        key2 = scorer._compute_sort_key(moment2, 30.0)

        # Lower key = better (because we negate scores)
        assert key1[0] < key2[0]  # Primary sort by -total_score

    def test_compute_sort_key_prefers_middle(self, scorer):
        """When scores equal, should prefer middle of video."""
        # Both have same score
        moment_start = MomentScore(
            start_time=0,
            end_time=5,
            total_score=0.5,
            face_score=0.5,
            motion_score=0.5,
            stability_score=0.5,
        )
        moment_middle = MomentScore(
            start_time=12.5,
            end_time=17.5,
            total_score=0.5,
            face_score=0.5,
            motion_score=0.5,
            stability_score=0.5,
        )

        key_start = scorer._compute_sort_key(moment_start, 30.0)
        key_middle = scorer._compute_sort_key(moment_middle, 30.0)

        # Primary score should be equal
        assert key_start[0] == key_middle[0]
        # Middle should have lower distance from midpoint (key[2])
        assert key_middle[2] < key_start[2]

    def test_compute_sort_key_prefers_variance(self, scorer):
        """When scores equal, should prefer higher component variance."""
        # Same total, but different component distribution
        moment_uniform = MomentScore(
            start_time=0,
            end_time=5,
            total_score=0.5,
            face_score=0.5,
            motion_score=0.5,
            stability_score=0.5,
        )
        moment_varied = MomentScore(
            start_time=0,
            end_time=5,
            total_score=0.5,
            face_score=0.9,
            motion_score=0.2,
            stability_score=0.4,
        )

        key_uniform = scorer._compute_sort_key(moment_uniform, 30.0)
        key_varied = scorer._compute_sort_key(moment_varied, 30.0)

        # Primary score should be equal
        assert key_uniform[0] == key_varied[0]
        # Varied should have lower (more negative) variance key
        assert key_varied[1] < key_uniform[1]


class TestSampleAndScoreVideo:
    """Tests for sample_and_score_video method."""

    @pytest.fixture
    def scorer(self):
        """Create a SceneScorer instance with mocked face detection."""
        scorer = SceneScorer()
        scorer._use_vision = False
        scorer._face_cascade = None  # Skip face detection
        return scorer

    def test_sample_video_file_not_found(self, scorer):
        """Should raise FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            scorer.sample_and_score_video(Path("/nonexistent/video.mp4"))

    def test_sample_video_empty_duration(self, scorer):
        """Should return empty list for zero duration."""
        with patch("immich_memories.analysis.scoring.get_video_info") as mock_info:
            mock_info.return_value = {"duration": 0}

            with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
                path = Path(f.name)
                results = scorer.sample_and_score_video(path)

            assert results == []

    def test_sample_video_sorted_by_score(self, scorer):
        """Results should be sorted by score (best first)."""
        with (
            patch.object(scorer, "_generate_segments") as mock_gen,
            patch.object(scorer, "score_scene") as mock_score,
            patch("immich_memories.analysis.scoring.get_video_info") as mock_info,
        ):
            mock_info.return_value = {"duration": 15.0}

            # Mock segments
            mock_gen.return_value = [
                Scene(start_time=0, end_time=5, start_frame=0, end_frame=150),
                Scene(start_time=5, end_time=10, start_frame=150, end_frame=300),
                Scene(start_time=10, end_time=15, start_frame=300, end_frame=450),
            ]

            # Mock scores - middle one is best
            mock_score.side_effect = [
                MomentScore(
                    start_time=0,
                    end_time=5,
                    total_score=0.5,
                    face_score=0.5,
                    motion_score=0.5,
                    stability_score=0.5,
                ),
                MomentScore(
                    start_time=5,
                    end_time=10,
                    total_score=0.8,
                    face_score=0.8,
                    motion_score=0.8,
                    stability_score=0.8,
                ),
                MomentScore(
                    start_time=10,
                    end_time=15,
                    total_score=0.6,
                    face_score=0.6,
                    motion_score=0.6,
                    stability_score=0.6,
                ),
            ]

            with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
                path = Path(f.name)
                results = scorer.sample_and_score_video(path)

            # Best score should be first
            assert results[0].total_score == 0.8
            assert results[0].start_time == 5.0


class TestSceneAwareSegmentation:
    """Tests for scene-aware segmentation."""

    @pytest.fixture
    def scorer(self):
        """Create a SceneScorer instance."""
        return SceneScorer()

    def test_subdivide_scene_short_scene(self, scorer):
        """Short scene >= 50% of target should be returned as-is."""
        scene = Scene(start_time=0, end_time=4, start_frame=0, end_frame=120)

        # Target duration of 5s is longer than scene (4s)
        # Since 4s >= 2.5s (50% of 5s), the scene is returned as a single segment
        segments = scorer._subdivide_scene(scene, target_duration=5.0, overlap=0.5, fps=30)

        assert len(segments) == 1
        assert segments[0].start_time == 0
        assert segments[0].end_time == 4

    def test_subdivide_scene_very_short_scene(self, scorer):
        """Very short scene < 50% of target should return empty list."""
        scene = Scene(start_time=0, end_time=2, start_frame=0, end_frame=60)

        # Target duration of 5s, scene is only 2s (< 50%)
        segments = scorer._subdivide_scene(scene, target_duration=5.0, overlap=0.5, fps=30)

        # Scene is too short to meet 50% threshold
        assert len(segments) == 0

    def test_subdivide_scene_exact_fit(self, scorer):
        """Scene exactly matching target returns overlapping segments due to step size."""
        scene = Scene(start_time=0, end_time=5, start_frame=0, end_frame=150)

        # With 5s target and 50% overlap, step = 2.5s
        # First segment: 0-5s
        # Step forward: 2.5s, but 2.5+5 > 5, so no more full segments
        # Remaining (5-2.5=2.5s) >= 50% of 5s, so it gets added as partial
        segments = scorer._subdivide_scene(scene, target_duration=5.0, overlap=0.5, fps=30)

        assert len(segments) == 2
        assert segments[0].start_time == 0
        assert segments[0].end_time == 5
        # Second is a partial segment starting at step position
        assert segments[1].start_time == 2.5
        assert segments[1].end_time == 5

    def test_subdivide_scene_long_scene(self, scorer):
        """Long scene should be subdivided with overlap."""
        scene = Scene(start_time=0, end_time=20, start_frame=0, end_frame=600)

        segments = scorer._subdivide_scene(scene, target_duration=5.0, overlap=0.5, fps=30)

        # With 5s target and 50% overlap, step = 2.5s
        # Segments: 0-5, 2.5-7.5, 5-10, 7.5-12.5, 10-15, 12.5-17.5, 15-20
        assert len(segments) >= 6
        assert segments[0].start_time == 0
        assert segments[0].end_time == 5
        assert segments[1].start_time == 2.5
        assert segments[1].end_time == 7.5
        # All segments stay within original scene boundaries
        for seg in segments:
            assert seg.start_time >= scene.start_time
            assert seg.end_time <= scene.end_time

    def test_subdivide_scene_preserves_frame_boundaries(self, scorer):
        """Frame numbers should be calculated correctly."""
        scene = Scene(start_time=10, end_time=25, start_frame=300, end_frame=750)

        segments = scorer._subdivide_scene(scene, target_duration=5.0, overlap=0.5, fps=30)

        # First segment should start at scene start
        assert segments[0].start_time == 10
        assert segments[0].start_frame == int(10 * 30)

    def test_generate_scene_aware_segments_filters_short_scenes(self, scorer):
        """Should filter out scenes shorter than min_segment_duration."""
        with patch("immich_memories.analysis.scenes.SceneDetector") as mock_detector_class:
            mock_detector = mock_detector_class.return_value
            mock_detector.detect.return_value = [
                Scene(start_time=0, end_time=0.5, start_frame=0, end_frame=15),  # Too short
                Scene(start_time=1, end_time=6, start_frame=30, end_frame=180),  # Good
                Scene(start_time=7, end_time=8, start_frame=210, end_frame=240),  # Too short
            ]

            with (
                patch("immich_memories.analysis.scoring.get_video_info") as mock_info,
                tempfile.NamedTemporaryFile(suffix=".mp4") as f,
            ):
                mock_info.return_value = {"duration": 10.0, "fps": 30}
                segments = scorer._generate_scene_aware_segments(
                    video_path=Path(f.name),
                    max_segment_duration=10.0,
                    min_segment_duration=1.5,
                    scene_threshold=27.0,
                    min_scene_duration=1.0,
                )

            # Only the 5-second scene should remain
            assert len(segments) == 1
            assert segments[0].start_time == 1
            assert segments[0].end_time == 6

    def test_generate_scene_aware_segments_subdivides_long_scenes(self, scorer):
        """Should subdivide scenes longer than max_segment_duration."""
        with patch("immich_memories.analysis.scenes.SceneDetector") as mock_detector_class:
            mock_detector = mock_detector_class.return_value
            mock_detector.detect.return_value = [
                Scene(start_time=0, end_time=25, start_frame=0, end_frame=750),  # Very long
            ]

            with (
                patch("immich_memories.analysis.scoring.get_video_info") as mock_info,
                tempfile.NamedTemporaryFile(suffix=".mp4") as f,
            ):
                mock_info.return_value = {"duration": 25.0, "fps": 30}
                segments = scorer._generate_scene_aware_segments(
                    video_path=Path(f.name),
                    max_segment_duration=10.0,
                    min_segment_duration=1.5,
                    scene_threshold=27.0,
                    min_scene_duration=1.0,
                )

            # 25s scene with 10s max should be subdivided into multiple segments
            assert len(segments) > 1
            # All segments should be within original scene bounds
            for seg in segments:
                assert seg.start_time >= 0
                assert seg.end_time <= 25

    def test_sample_and_score_video_uses_scene_detection_by_default(self, scorer):
        """Should use scene detection when config enables it."""
        with (
            patch("immich_memories.config.get_config") as mock_config,
            patch.object(scorer, "_generate_scene_aware_segments") as mock_scene,
            patch.object(scorer, "_generate_segments") as mock_fixed,
            patch("immich_memories.analysis.scoring.get_video_info") as mock_info,
        ):
            # Config says use scene detection (default)
            mock_config.return_value.analysis.use_scene_detection = True
            mock_config.return_value.analysis.max_segment_duration = 10.0
            mock_config.return_value.analysis.min_segment_duration = 1.5
            mock_config.return_value.analysis.scene_threshold = 27.0
            mock_config.return_value.analysis.min_scene_duration = 1.0
            mock_info.return_value = {"duration": 10.0}
            mock_scene.return_value = []

            with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
                scorer.sample_and_score_video(Path(f.name))

            mock_scene.assert_called_once()
            mock_fixed.assert_not_called()

    def test_sample_and_score_video_respects_explicit_disable(self, scorer):
        """Should use fixed segments when explicitly disabled."""
        with (
            patch("immich_memories.config.get_config") as mock_config,
            patch.object(scorer, "_generate_scene_aware_segments") as mock_scene,
            patch.object(scorer, "_generate_segments") as mock_fixed,
            patch("immich_memories.analysis.scoring.get_video_info") as mock_info,
        ):
            # Config says use scene detection, but we override
            mock_config.return_value.analysis.use_scene_detection = True
            mock_info.return_value = {"duration": 10.0}
            mock_fixed.return_value = []

            with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
                # Explicitly disable scene detection
                scorer.sample_and_score_video(Path(f.name), use_scene_detection=False)

            mock_fixed.assert_called_once()
            mock_scene.assert_not_called()

    def test_sample_and_score_video_fallback_on_error(self, scorer):
        """Should fall back to fixed segments if scene detection fails."""
        with (
            patch("immich_memories.config.get_config") as mock_config,
            patch.object(scorer, "_generate_scene_aware_segments") as mock_scene,
            patch.object(scorer, "_generate_segments") as mock_fixed,
            patch("immich_memories.analysis.scoring.get_video_info") as mock_info,
        ):
            # Config says use scene detection
            mock_config.return_value.analysis.use_scene_detection = True
            mock_config.return_value.analysis.max_segment_duration = 10.0
            mock_config.return_value.analysis.min_segment_duration = 1.5
            mock_config.return_value.analysis.scene_threshold = 27.0
            mock_config.return_value.analysis.min_scene_duration = 1.0
            mock_info.return_value = {"duration": 10.0}

            # Make scene detection fail
            mock_scene.side_effect = RuntimeError("PySceneDetect not available")
            mock_fixed.return_value = []

            with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
                scorer.sample_and_score_video(Path(f.name))

            # Should have fallen back to fixed segments
            mock_scene.assert_called_once()
            mock_fixed.assert_called_once()


class TestSampleVideoConvenienceFunction:
    """Tests for sample_video convenience function."""

    def test_sample_video_creates_scorer(self):
        """Should create a SceneScorer and call sample_and_score_video."""
        from immich_memories.analysis.scoring import sample_video

        with patch.object(SceneScorer, "sample_and_score_video") as mock_method:
            mock_method.return_value = []

            with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
                sample_video(Path(f.name), segment_duration=5.0, overlap=0.3)

            mock_method.assert_called_once()
            call_args = mock_method.call_args
            assert call_args.kwargs["segment_duration"] == 5.0
            assert call_args.kwargs["overlap"] == 0.3

    def test_sample_video_passes_use_scene_detection(self):
        """Should pass use_scene_detection parameter."""
        from immich_memories.analysis.scoring import sample_video

        with patch.object(SceneScorer, "sample_and_score_video") as mock_method:
            mock_method.return_value = []

            with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
                sample_video(Path(f.name), use_scene_detection=False)

            mock_method.assert_called_once()
            call_args = mock_method.call_args
            assert call_args.kwargs["use_scene_detection"] is False
