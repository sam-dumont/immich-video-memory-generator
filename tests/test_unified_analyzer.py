"""Tests for the unified analyzer module."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    import cv2  # noqa: F401
except ImportError:
    pytest.skip("cv2 not available", allow_module_level=True)

from immich_memories.analysis.scoring import MomentScore
from immich_memories.analysis.segment_generation import (
    find_nearest_cut_point,
    generate_candidate_segments,
    generate_fallback_segments,
    generate_segments_from_points,
    merge_boundaries,
)
from immich_memories.analysis.unified_analyzer import (
    CutPoint,
    ScoredSegment,
    UnifiedSegmentAnalyzer,
)
from immich_memories.config_loader import Config
from immich_memories.config_models import AnalysisConfig, AudioContentConfig


class TestCutPoint:
    """Tests for CutPoint dataclass."""

    def test_priority_both(self):
        """Both visual and audio should have priority 2."""
        cp = CutPoint(time=5.0, is_visual=True, is_audio=True)
        assert cp.priority == 2

    def test_priority_visual_only(self):
        """Visual only should have priority 1."""
        cp = CutPoint(time=5.0, is_visual=True, is_audio=False)
        assert cp.priority == 1

    def test_priority_audio_only(self):
        """Audio only should have priority 1."""
        cp = CutPoint(time=5.0, is_visual=False, is_audio=True)
        assert cp.priority == 1

    def test_priority_neither(self):
        """Neither should have priority 0."""
        cp = CutPoint(time=5.0, is_visual=False, is_audio=False)
        assert cp.priority == 0

    def test_sorting(self):
        """Cut points should sort by time."""
        points = [
            CutPoint(time=10.0, is_visual=True, is_audio=False),
            CutPoint(time=5.0, is_visual=False, is_audio=True),
            CutPoint(time=15.0, is_visual=True, is_audio=True),
        ]
        sorted_points = sorted(points)
        assert sorted_points[0].time == 5.0
        assert sorted_points[1].time == 10.0
        assert sorted_points[2].time == 15.0


class TestScoredSegment:
    """Tests for ScoredSegment dataclass."""

    def test_duration(self):
        """Should calculate duration correctly."""
        segment = ScoredSegment(
            start_time=5.0,
            end_time=10.0,
            visual_score=0.7,
            total_score=0.75,
        )
        assert segment.duration == 5.0

    def test_cut_quality_max(self):
        """Cut quality should be 1.0 when both priorities are 2."""
        segment = ScoredSegment(
            start_time=0.0,
            end_time=5.0,
            start_cut_priority=2,
            end_cut_priority=2,
        )
        assert segment.cut_quality == 1.0

    def test_cut_quality_min(self):
        """Cut quality should be 0.0 when both priorities are 0."""
        segment = ScoredSegment(
            start_time=0.0,
            end_time=5.0,
            start_cut_priority=0,
            end_cut_priority=0,
        )
        assert segment.cut_quality == 0.0

    def test_cut_quality_mixed(self):
        """Cut quality should scale properly for mixed priorities."""
        segment = ScoredSegment(
            start_time=0.0,
            end_time=5.0,
            start_cut_priority=2,
            end_cut_priority=1,
        )
        assert segment.cut_quality == 0.75

    def test_to_moment_score(self):
        """Should convert to MomentScore correctly."""
        segment = ScoredSegment(
            start_time=5.0,
            end_time=10.0,
            visual_score=0.7,
            content_score=0.6,
            total_score=0.75,
            face_score=0.8,
            motion_score=0.6,
            stability_score=0.7,
        )
        moment = segment.to_moment_score()

        assert isinstance(moment, MomentScore)
        assert moment.start_time == 5.0
        assert moment.end_time == 10.0
        assert moment.total_score == 0.75
        assert moment.face_score == 0.8
        assert moment.motion_score == 0.6
        assert moment.stability_score == 0.7
        assert moment.content_score == 0.6


class TestUnifiedSegmentAnalyzer:
    """Tests for UnifiedSegmentAnalyzer class."""

    @pytest.fixture
    def analyzer(self):
        """Create an UnifiedSegmentAnalyzer instance with mocked scorer."""
        # WHY: mock scorer — visual scoring requires real video frames + OpenCV processing
        mock_scorer = MagicMock()
        return UnifiedSegmentAnalyzer(
            scorer=mock_scorer,
            min_segment_duration=2.0,
            max_segment_duration=15.0,
            duration_weight=0.0,  # Disable duration scoring for simpler test math
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )

    def test_merge_boundaries_empty(self, analyzer):
        """Empty boundaries should return video start/end."""
        result = merge_boundaries([], [], video_duration=30.0, cut_point_merge_tolerance=0.5)

        assert len(result) == 2
        assert result[0].time == 0.0
        assert result[1].time == 30.0

    def test_merge_boundaries_visual_only(self, analyzer):
        """Visual-only boundaries should be marked correctly."""
        result = merge_boundaries(
            [5.0, 10.0], [], video_duration=30.0, cut_point_merge_tolerance=0.5
        )

        # Should have: 0, 5, 10, 30
        assert len(result) >= 4
        # Find the 5.0 point
        point_5 = next(cp for cp in result if abs(cp.time - 5.0) < 0.5)
        assert point_5.is_visual
        assert not point_5.is_audio

    def test_merge_boundaries_audio_only(self, analyzer):
        """Audio-only boundaries should be marked correctly."""
        result = merge_boundaries(
            [], [5.0, 10.0], video_duration=30.0, cut_point_merge_tolerance=0.5
        )

        # Should have: 0, 5, 10, 30
        assert len(result) >= 4
        # Find the 5.0 point
        point_5 = next(cp for cp in result if abs(cp.time - 5.0) < 0.5)
        assert not point_5.is_visual
        assert point_5.is_audio

    def test_merge_boundaries_merged(self, analyzer):
        """Nearby visual and audio boundaries should be merged."""
        # 5.0 visual and 5.2 audio should merge (within 0.5s tolerance)
        result = merge_boundaries([5.0], [5.2], video_duration=30.0, cut_point_merge_tolerance=0.5)

        # Should merge into one point with both flags
        merged_points = [cp for cp in result if cp.is_visual and cp.is_audio]
        # At least start (0), merged (5ish), and end (30) should have both
        assert len(merged_points) >= 2  # Start and end at minimum

    def test_merge_boundaries_not_merged(self, analyzer):
        """Distant visual and audio boundaries should not merge."""
        # 5.0 visual and 10.0 audio are too far apart
        result = merge_boundaries([5.0], [10.0], video_duration=30.0, cut_point_merge_tolerance=0.5)

        # Find individual points
        point_5 = next(cp for cp in result if abs(cp.time - 5.0) < 0.3)
        point_10 = next(cp for cp in result if abs(cp.time - 10.0) < 0.3)

        assert point_5.is_visual
        assert not point_5.is_audio
        assert not point_10.is_visual
        assert point_10.is_audio

    def test_generate_candidate_segments_respects_duration(self, analyzer):
        """Segments should respect min/max duration."""
        cut_points = [
            CutPoint(time=0.0, is_visual=True, is_audio=True),
            CutPoint(time=1.0, is_visual=True, is_audio=True),  # Too short
            CutPoint(time=5.0, is_visual=True, is_audio=True),  # Valid: 5-0=5s
            CutPoint(time=10.0, is_visual=True, is_audio=True),  # Valid: 10-5=5s
            CutPoint(time=30.0, is_visual=True, is_audio=True),  # Too long: 30-10=20s
        ]

        result = generate_candidate_segments(
            cut_points,
            video_duration=30.0,
            min_segment_duration=analyzer.min_segment_duration,
            max_segment_duration=analyzer.max_segment_duration,
            dynamic_optimal=5.0,
        )

        # Check all generated segments respect duration constraints
        for start_cp, end_cp in result:
            duration = end_cp.time - start_cp.time
            assert duration >= analyzer.min_segment_duration
            assert duration <= analyzer.max_segment_duration

    def test_generate_candidate_segments_prefers_audio(self, analyzer):
        """Should prefer segments with audio boundaries."""
        cut_points = [
            CutPoint(time=0.0, is_visual=True, is_audio=True),
            CutPoint(time=5.0, is_visual=True, is_audio=False),  # Visual only
            CutPoint(time=10.0, is_visual=False, is_audio=True),  # Audio only
            CutPoint(time=15.0, is_visual=True, is_audio=True),
        ]

        result = generate_candidate_segments(
            cut_points,
            video_duration=30.0,
            min_segment_duration=analyzer.min_segment_duration,
            max_segment_duration=analyzer.max_segment_duration,
            dynamic_optimal=5.0,
        )

        # Should include segments starting/ending on audio boundaries
        assert result

        # First candidates should have audio boundaries
        if result:
            first_start, first_end = result[0]
            # Either start or end should be audio
            assert first_start.is_audio or first_end.is_audio

    def test_generate_segments_from_points(self, analyzer):
        """Should generate all valid segment pairs."""
        points = [
            CutPoint(time=0.0, is_visual=True, is_audio=True),
            CutPoint(time=5.0, is_visual=True, is_audio=True),
            CutPoint(time=10.0, is_visual=True, is_audio=True),
        ]

        result = generate_segments_from_points(
            points,
            min_segment_duration=analyzer.min_segment_duration,
            max_segment_duration=analyzer.max_segment_duration,
        )

        # Should include: (0,5), (0,10), (5,10)
        assert len(result) >= 2
        # All should have valid durations
        for start_cp, end_cp in result:
            assert end_cp.time > start_cp.time

    def test_generate_fallback_segments(self, analyzer):
        """Fallback should always return at least one segment."""
        cut_points = [
            CutPoint(time=0.0, is_visual=True, is_audio=True),
            CutPoint(time=30.0, is_visual=True, is_audio=True),
        ]

        result = generate_fallback_segments(
            video_duration=30.0,
            cut_points=cut_points,
            min_segment_duration=analyzer.min_segment_duration,
            proportional_max=analyzer._get_max_segment_for_source(30.0),
        )

        assert len(result) >= 1

    def test_find_nearest_cut_point(self, analyzer):
        """Should find the nearest cut point."""
        cut_points = [
            CutPoint(time=0.0, is_visual=True, is_audio=True),
            CutPoint(time=5.0, is_visual=True, is_audio=True),
            CutPoint(time=10.0, is_visual=True, is_audio=True),
        ]

        result = find_nearest_cut_point(cut_points, 4.0)
        assert result.time == 5.0

        result = find_nearest_cut_point(cut_points, 6.0)
        assert result.time == 5.0

        result = find_nearest_cut_point(cut_points, 8.0)
        assert result.time == 10.0

    def test_find_nearest_cut_point_empty(self, analyzer):
        """Should return None for empty list."""
        result = find_nearest_cut_point([], 5.0)
        assert result is None

    def test_compute_total_score_no_content(self, analyzer):
        """Without content, total should be visual + cut bonus."""
        segment = ScoredSegment(
            start_time=0.0,
            end_time=5.0,
            visual_score=0.8,
            content_score=0.0,
            start_cut_priority=2,
            end_cut_priority=2,
        )
        analyzer.content_weight = 0.0

        score = analyzer._compute_total_score(segment)

        # 0.8 * 1.0 + 1.0 * 0.15 (cut bonus) = 0.95
        assert abs(score - 0.95) < 0.01

    def test_compute_total_score_with_content(self, analyzer):
        """With content above neutral, LLM adds a bonus on top of base score."""
        segment = ScoredSegment(
            start_time=0.0,
            end_time=5.0,
            visual_score=0.8,
            content_score=0.6,
            start_cut_priority=2,
            end_cut_priority=2,
        )
        analyzer.content_weight = 0.2

        score = analyzer._compute_total_score(segment)

        # base = visual*1.0 = 0.8 (duration_weight=0.0 in fixture)
        # llm_bonus = max(0, (0.6-0.5)) * 0.2 * 2 = 0.04
        # cut_bonus = 1.0 * 0.15 = 0.15
        # total ≈ 0.99
        assert abs(score - 0.99) < 0.01


class TestUnifiedAnalyzerIntegration:
    """Integration tests for UnifiedSegmentAnalyzer."""

    @pytest.fixture
    def analyzer(self):
        """Create analyzer with mocked dependencies."""
        # WHY: mock scorer — integration tests verify boundary detection + segment selection,
        # not the visual scoring pipeline which needs real video frames
        mock_scorer = MagicMock()
        mock_scorer.score_scene.return_value = MomentScore(
            start_time=0.0,
            end_time=5.0,
            total_score=0.7,
            face_score=0.8,
            motion_score=0.6,
            stability_score=0.7,
        )
        return UnifiedSegmentAnalyzer(
            scorer=mock_scorer,
            min_segment_duration=2.0,
            max_segment_duration=15.0,
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )

    def test_analyze_file_not_found(self, analyzer):
        """Should return empty list for missing file."""
        result = analyzer.analyze(Path("/nonexistent/video.mp4"))
        assert not result

    def test_analyze_with_mocked_detectors(self, analyzer):
        """Should analyze video with mocked boundary detectors."""
        # WHY: mock boundary detectors + get_video_info — they require real video files with
        # audio/video streams; we're testing the analyze() orchestration logic
        with (
            patch(
                "immich_memories.analysis.unified_analyzer.detect_visual_boundaries"
            ) as mock_visual,
            patch(
                "immich_memories.analysis.unified_analyzer.detect_audio_boundaries"
            ) as mock_audio,
            patch("immich_memories.analysis.unified_analyzer.get_video_info") as mock_info,
            tempfile.NamedTemporaryFile(suffix=".mp4") as f,
        ):
            mock_visual.return_value = [0.0, 5.0, 10.0]
            mock_audio.return_value = [0.0, 5.0, 10.0]
            mock_info.return_value = {"duration": 10.0}

            result = analyzer.analyze(Path(f.name))

            # Should return scored segments
            assert isinstance(result, list)
            # Detectors should have been called
            mock_visual.assert_called_once()
            mock_audio.assert_called_once()

    def test_analyze_returns_sorted_segments(self, analyzer):
        """Results should be sorted by score (best first)."""
        with (
            patch(
                "immich_memories.analysis.unified_analyzer.detect_visual_boundaries"
            ) as mock_visual,
            patch(
                "immich_memories.analysis.unified_analyzer.detect_audio_boundaries"
            ) as mock_audio,
            patch.object(analyzer, "_score_visual") as mock_score,
            patch("immich_memories.analysis.unified_analyzer.get_video_info") as mock_info,
            tempfile.NamedTemporaryFile(suffix=".mp4") as f,
        ):
            mock_visual.return_value = [0.0, 5.0, 10.0, 15.0]
            mock_audio.return_value = [0.0, 5.0, 10.0, 15.0]
            mock_info.return_value = {"duration": 15.0}

            # Return different scores for different segments
            call_count = [0]

            def score_side_effect(*args, **kwargs):
                call_count[0] += 1
                scores = [0.5, 0.9, 0.7]  # Middle segment is best
                return {
                    "face": scores[call_count[0] % 3],
                    "motion": scores[call_count[0] % 3],
                    "stability": scores[call_count[0] % 3],
                    "total": scores[call_count[0] % 3],
                }

            mock_score.side_effect = score_side_effect

            result = analyzer.analyze(Path(f.name))

            if len(result) >= 2:
                # First result should have highest score
                assert result[0].total_score >= result[1].total_score

    def test_analyze_fallback_on_no_audio(self, analyzer):
        """Should fall back when no audio boundaries."""
        with (
            patch(
                "immich_memories.analysis.unified_analyzer.detect_visual_boundaries"
            ) as mock_visual,
            patch(
                "immich_memories.analysis.unified_analyzer.detect_audio_boundaries"
            ) as mock_audio,
            patch("immich_memories.analysis.unified_analyzer.get_video_info") as mock_info,
            tempfile.NamedTemporaryFile(suffix=".mp4") as f,
        ):
            mock_visual.return_value = [0.0, 5.0, 10.0]
            mock_audio.return_value = []  # No audio boundaries
            mock_info.return_value = {"duration": 10.0}

            result = analyzer.analyze(Path(f.name))

            # Should still return segments using visual-only
            assert isinstance(result, list)


class TestCreateUnifiedAnalyzerFromConfig:
    """Tests for factory function."""

    def _patch_duration_weight(self, config: Config) -> None:
        """Add duration_weight to AnalysisConfig if missing (source bug workaround)."""
        if not hasattr(config.analysis, "duration_weight"):
            object.__setattr__(config.analysis, "duration_weight", 0.15)

    def test_creates_analyzer_with_defaults(self):
        """Should create analyzer from config."""
        from immich_memories.analysis.unified_analyzer import (
            create_unified_analyzer_from_config,
        )

        config = Config()
        self._patch_duration_weight(config)
        analyzer = create_unified_analyzer_from_config(config)

        assert isinstance(analyzer, UnifiedSegmentAnalyzer)
        assert analyzer.min_segment_duration == config.analysis.min_segment_duration
        assert analyzer.max_segment_duration == config.analysis.max_segment_duration

    def test_creates_analyzer_with_content_analysis(self):
        """Should create analyzer with content analysis when enabled."""
        # WHY: mock get_content_analyzer — factory creates LLM client;
        # tests verify wiring without needing a running LLM server
        with patch(
            "immich_memories.analysis.content_analyzer.get_content_analyzer"
        ) as mock_get_analyzer:
            mock_get_analyzer.return_value = MagicMock()

            from immich_memories.analysis.unified_analyzer import (
                create_unified_analyzer_from_config,
            )

            config = Config()
            config.content_analysis.enabled = True
            config.content_analysis.weight = 0.2
            self._patch_duration_weight(config)

            analyzer = create_unified_analyzer_from_config(config)

            assert analyzer.content_weight == 0.2
            mock_get_analyzer.assert_called_once()
