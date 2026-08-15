"""Tests for the unified analyzer module."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
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
from immich_memories.audio.audio_models import AudioAnalysisResult
from immich_memories.config_loader import Config
from immich_memories.config_models import AnalysisConfig, AudioContentConfig, SpeechConfig
from immich_memories.speech.models import SpeechRegion


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


class TestResetForVideo:
    def test_releases_capture_and_clears_audio_cache_without_destroying_models(self):
        scorer = MagicMock()
        audio_analyzer = MagicMock()
        content_analyzer = MagicMock()
        analyzer = UnifiedSegmentAnalyzer(
            scorer=scorer,
            content_analyzer=content_analyzer,
            audio_analyzer=audio_analyzer,
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )
        analyzer._audio_analysis_cache["first-video"] = MagicMock()

        analyzer.reset_for_video()

        assert analyzer._audio_analysis_cache == {}
        assert analyzer.content_analyzer is content_analyzer
        assert analyzer._audio_analyzer is audio_analyzer
        scorer.release_capture.assert_called_once()


class TestPerInvocationAnalysisModes:
    def test_legacy_mode_skips_content_and_audio_then_unified_mode_restores_them(
        self, tmp_path: Path, monkeypatch
    ):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"video")
        audio_analyzer = MagicMock()
        audio_analyzer.analyze.return_value = MagicMock(
            audio_score=0.5,
            has_laughter=False,
            has_speech=False,
            protected_ranges=[],
        )
        content_analyzer = MagicMock()
        content_analyzer.analyze_segment.return_value = MagicMock(
            content_score=0.9,
            description="family at the beach",
            emotion="joy",
            setting="beach",
            activities=["swimming"],
            subjects=["family"],
            interestingness=0.9,
            quality=0.9,
        )
        analyzer = UnifiedSegmentAnalyzer(
            scorer=MagicMock(),
            content_analyzer=content_analyzer,
            content_weight=0.3,
            audio_content_enabled=True,
            audio_analyzer=audio_analyzer,
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )
        monkeypatch.setattr(
            "immich_memories.analysis.unified_analyzer.detect_visual_boundaries", lambda *_: []
        )
        monkeypatch.setattr(
            "immich_memories.analysis.unified_analyzer.detect_audio_boundaries", lambda *_: []
        )
        monkeypatch.setattr(
            "immich_memories.analysis.unified_analyzer.merge_boundaries", lambda *_: []
        )
        monkeypatch.setattr(
            "immich_memories.analysis.unified_analyzer.generate_candidate_segments", lambda *_: []
        )
        monkeypatch.setattr(
            "immich_memories.analysis.unified_analyzer.generate_fallback_segments", lambda *_: []
        )
        analyzer._score_segments_visual_only = MagicMock(
            return_value=[ScoredSegment(start_time=0.0, end_time=5.0, visual_score=0.6)]
        )

        analyzer.analyze(
            video,
            video_duration=10.0,
            enable_content_analysis=False,
            enable_audio_content_analysis=False,
        )

        audio_analyzer.analyze.assert_not_called()
        content_analyzer.analyze_segment.assert_not_called()
        analyzer._score_segments_visual_only.assert_called_once_with(
            video,
            [],
            [],
            None,
            10.0,
            enable_content_analysis=False,
            enable_audio_content_analysis=False,
        )

        analyzer.analyze(video, video_duration=10.0)

        audio_analyzer.analyze.assert_called_once()
        content_analyzer.analyze_segment.assert_called_once()
        assert analyzer._score_segments_visual_only.call_args.kwargs == {
            "enable_content_analysis": True,
            "enable_audio_content_analysis": True,
        }

        segment = ScoredSegment(
            start_time=0.0,
            end_time=5.0,
            visual_score=0.6,
            audio_score=1.0,
            content_score=1.0,
        )
        legacy_score = analyzer._compute_total_score(
            segment,
            enable_content_analysis=False,
            enable_audio_content_analysis=False,
        )
        visual_only = UnifiedSegmentAnalyzer(
            scorer=MagicMock(),
            content_weight=0.0,
            audio_content_enabled=False,
            audio_content_weight=analyzer.audio_content_weight,
            duration_weight=analyzer.duration_weight,
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )
        assert legacy_score == pytest.approx(visual_only._compute_total_score(segment))

        unified_score = analyzer._compute_total_score(
            segment,
            enable_content_analysis=True,
            enable_audio_content_analysis=True,
        )
        assert unified_score > legacy_score


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
        # config.speech must reach the analyzer the same way config.audio_content does.
        assert analyzer._speech_config is config.speech

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


class TestScoreVisualExcludesAudio:
    """Visual score should only include face, motion, stability — not audio."""

    def test_visual_score_excludes_audio_weight(self):
        # WHY: mock scorer to verify the weighted average excludes audio_score
        mock_scorer = MagicMock()
        mock_scorer.score_scene.return_value = MomentScore(
            start_time=0.0,
            end_time=3.0,
            total_score=0.5,
            face_score=0.9,
            motion_score=0.6,
            stability_score=0.3,
            audio_score=0.5,
        )
        mock_scorer.face_weight = 0.35
        mock_scorer.motion_weight = 0.20
        mock_scorer.stability_weight = 0.15
        mock_scorer.audio_weight = 0.15

        analyzer = UnifiedSegmentAnalyzer(
            scorer=mock_scorer,
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )
        result = analyzer._score_visual(Path("/fake.mp4"), 0.0, 3.0)

        # Visual score = weighted average of face, motion, stability ONLY
        expected = (0.9 * 0.35 + 0.6 * 0.20 + 0.3 * 0.15) / (0.35 + 0.20 + 0.15)
        assert abs(result["total"] - expected) < 0.001


class TestVadDerivedProtectedRanges:
    """Noisy audio must not collapse into a single protected range."""

    def test_two_utterances_yield_two_protected_ranges(self):
        from immich_memories.analysis.unified_analyzer import protected_ranges_from_speech
        from immich_memories.speech.models import SpeechRegion

        regions = [SpeechRegion(0.5, 2.0), SpeechRegion(4.0, 6.0)]

        ranges = protected_ranges_from_speech(regions, max_duration=8.0)

        assert len(ranges) == 2
        assert ranges[0] == (0.5, 2.0)
        assert ranges[1] == (4.0, 6.0)

    def test_ranges_are_clamped_to_max_duration(self):
        from immich_memories.analysis.unified_analyzer import protected_ranges_from_speech
        from immich_memories.speech.models import SpeechRegion

        regions = [SpeechRegion(1.0, 99.0)]

        ranges = protected_ranges_from_speech(regions, max_duration=10.0)

        assert ranges == [(1.0, 10.0)]


class TestSpeechDetectorConstruction:
    """UnifiedSegmentAnalyzer builds a detector via `select_detector(speech_config)`."""

    def test_enabled_silero_config_constructs_detector(self):
        from immich_memories.speech.vad import SileroSpeechDetector

        analyzer = UnifiedSegmentAnalyzer(
            scorer=MagicMock(),
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
            speech_config=SpeechConfig(
                enabled=True, engine="silero", vad_threshold=0.6, min_silence_ms=300
            ),
        )

        assert isinstance(analyzer._speech_detector, SileroSpeechDetector)
        assert analyzer._speech_detector.threshold == 0.6
        assert analyzer._speech_detector.min_silence_ms == 300

    def test_enabled_fireredvad_config_constructs_detector(self):
        from immich_memories.speech.fireredvad import FireRedSpeechDetector

        analyzer = UnifiedSegmentAnalyzer(
            scorer=MagicMock(),
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
            speech_config=SpeechConfig(
                enabled=True, engine="fireredvad", vad_threshold=0.6, min_silence_ms=300
            ),
        )

        assert isinstance(analyzer._speech_detector, FireRedSpeechDetector)
        assert analyzer._speech_detector.threshold == 0.6
        assert analyzer._speech_detector.min_silence_ms == 300

    def test_energy_engine_has_no_detector(self):
        analyzer = UnifiedSegmentAnalyzer(
            scorer=MagicMock(),
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
            speech_config=SpeechConfig(enabled=True, engine="energy"),
        )

        assert analyzer._speech_detector is None

    def test_disabled_config_skips_detector(self):
        analyzer = UnifiedSegmentAnalyzer(
            scorer=MagicMock(),
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
            speech_config=SpeechConfig(enabled=False),
        )

        assert analyzer._speech_detector is None

    def test_missing_speech_config_defaults_to_fireredvad(self):
        # No speech_config passed -- SpeechConfig() defaults apply: enabled,
        # engine="fireredvad".
        from immich_memories.speech.fireredvad import FireRedSpeechDetector

        analyzer = UnifiedSegmentAnalyzer(
            scorer=MagicMock(),
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )

        assert isinstance(analyzer._speech_detector, FireRedSpeechDetector)


class TestApplyVadRanges:
    """`_apply_vad_ranges` replaces PANNs-derived protected ranges with VAD ones."""

    def _analyzer(self, **kwargs) -> UnifiedSegmentAnalyzer:
        return UnifiedSegmentAnalyzer(
            scorer=MagicMock(),
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
            **kwargs,
        )

    def test_disabled_speech_config_leaves_result_untouched(self):
        analyzer = self._analyzer(speech_config=SpeechConfig(enabled=False))
        result = AudioAnalysisResult(protected_ranges=[(0.0, 8.0)])

        updated = analyzer._apply_vad_ranges(Path("/fake.mov"), result, video_duration=8.0)

        assert updated.protected_ranges == [(0.0, 8.0)]

    def test_extraction_failure_leaves_result_untouched(self):
        analyzer = self._analyzer()
        analyzer._speech_detector = MagicMock()  # should never be reached
        result = AudioAnalysisResult(protected_ranges=[(0.0, 8.0)])

        # WHY: replaces extract_audio_16k (the FFmpeg boundary) so the "no
        # audio extracted" branch is exercised without a real video file.
        with patch(
            "immich_memories.analysis.unified_analyzer.extract_audio_16k", return_value=None
        ):
            updated = analyzer._apply_vad_ranges(Path("/fake.mov"), result, video_duration=8.0)

        assert updated.protected_ranges == [(0.0, 8.0)]
        analyzer._speech_detector.detect.assert_not_called()

    def test_no_speech_regions_leaves_result_untouched(self):
        analyzer = self._analyzer()

        class _EmptyDetector:
            def detect(self, audio, sample_rate):
                return []

        analyzer._speech_detector = _EmptyDetector()
        result = AudioAnalysisResult(protected_ranges=[(0.0, 8.0)])
        audio = np.zeros(16000 * 8, dtype=np.float32)

        with patch(
            "immich_memories.analysis.unified_analyzer.extract_audio_16k", return_value=audio
        ):
            updated = analyzer._apply_vad_ranges(Path("/fake.mov"), result, video_duration=8.0)

        assert updated.protected_ranges == [(0.0, 8.0)]

    def test_vad_regions_break_up_the_panns_blob(self):
        analyzer = self._analyzer()

        class _TwoUtteranceDetector:
            def detect(self, audio, sample_rate):
                return [SpeechRegion(0.5, 2.0), SpeechRegion(4.0, 6.0)]

        analyzer._speech_detector = _TwoUtteranceDetector()
        # PANNs collapsed the whole clip into one protected range -- this is
        # exactly the blob VAD is meant to break up.
        result = AudioAnalysisResult(protected_ranges=[(0.0, 8.0)])
        audio = np.zeros(16000 * 8, dtype=np.float32)

        with patch(
            "immich_memories.analysis.unified_analyzer.extract_audio_16k", return_value=audio
        ):
            updated = analyzer._apply_vad_ranges(Path("/fake.mov"), result, video_duration=8.0)

        assert updated.protected_ranges == [(0.5, 2.0), (4.0, 6.0)]

    def test_extraction_runs_once_per_video_path(self):
        analyzer = self._analyzer()

        class _TwoUtteranceDetector:
            def detect(self, audio, sample_rate):
                return [SpeechRegion(0.5, 2.0), SpeechRegion(4.0, 6.0)]

        analyzer._speech_detector = _TwoUtteranceDetector()
        audio = np.zeros(16000 * 8, dtype=np.float32)
        video_path = Path("/fake.mov")

        # WHY: replaces extract_audio_16k (the FFmpeg boundary) to prove the
        # analyzer's own cache -- not FFmpeg's absence -- explains the count.
        with patch(
            "immich_memories.analysis.unified_analyzer.extract_audio_16k", return_value=audio
        ) as extract:
            first = analyzer._apply_vad_ranges(
                video_path, AudioAnalysisResult(protected_ranges=[(0.0, 8.0)]), 8.0
            )
            second = analyzer._apply_vad_ranges(
                video_path, AudioAnalysisResult(protected_ranges=[(0.0, 8.0)]), 8.0
            )

        extract.assert_called_once()
        assert first.protected_ranges == [(0.5, 2.0), (4.0, 6.0)]
        assert second.protected_ranges == [(0.5, 2.0), (4.0, 6.0)]
        assert list(analyzer._vad_audio_cache) == [str(video_path)]


class TestAnalyzeAudioContentAppliesVad:
    """`_analyze_audio_content` wires PANNs analysis through `_apply_vad_ranges`."""

    def test_vad_override_is_cached_on_the_final_result(self, tmp_path: Path):
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"fake")

        # WHY: mock audio_analyzer -- PANNs classification needs the real
        # torch model; the panns_blob shape (one range spanning the clip)
        # is all this test needs.
        audio_analyzer = MagicMock()
        audio_analyzer.analyze.return_value = AudioAnalysisResult(protected_ranges=[(0.0, 8.0)])

        analyzer = UnifiedSegmentAnalyzer(
            scorer=MagicMock(),
            audio_analyzer=audio_analyzer,
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )

        class _TwoUtteranceDetector:
            def detect(self, audio, sample_rate):
                return [SpeechRegion(0.5, 2.0), SpeechRegion(4.0, 6.0)]

        analyzer._speech_detector = _TwoUtteranceDetector()
        audio = np.zeros(16000 * 8, dtype=np.float32)

        with patch(
            "immich_memories.analysis.unified_analyzer.extract_audio_16k", return_value=audio
        ):
            result = analyzer._analyze_audio_content(video_path, video_duration=8.0)

        assert result.protected_ranges == [(0.5, 2.0), (4.0, 6.0)]
        assert analyzer._audio_analysis_cache[str(video_path)].protected_ranges == [
            (0.5, 2.0),
            (4.0, 6.0),
        ]
