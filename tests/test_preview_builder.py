"""Tests for PreviewBuilder.run_legacy_analysis — verifies unified analyzer routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from immich_memories.analysis.analyzer_models import ScoredSegment
from immich_memories.analysis.preview_builder import PreviewBuilder
from immich_memories.config_models import AnalysisConfig, CacheConfig, ContentAnalysisConfig


def _make_builder() -> PreviewBuilder:
    return PreviewBuilder(
        client=MagicMock(),
        cache_config=CacheConfig(),
        analysis_config=AnalysisConfig(),
        content_analysis_config=ContentAnalysisConfig(),
    )


def _make_segment(start: float, end: float, score: float) -> ScoredSegment:
    return ScoredSegment(
        start_time=start,
        end_time=end,
        total_score=score,
        visual_score=score,
        audio_score=0.5,
        duration_score=0.5,
        face_score=0.6,
        motion_score=0.4,
        stability_score=0.5,
    )


class TestRunLegacyAnalysis:
    def test_returns_best_segment(self):
        builder = _make_builder()
        seg = _make_segment(1.0, 4.0, 0.8)

        # WHY: mock at source module — import is inside function body
        with patch(
            "immich_memories.analysis.unified_analyzer.UnifiedSegmentAnalyzer.analyze",
            return_value=[seg],
        ):
            clip = MagicMock()
            clip.duration_seconds = 10.0
            config = MagicMock()
            config.avg_clip_duration = 5.0
            cache = MagicMock()

            start, end, score = builder.run_legacy_analysis(
                clip, Path("/fake.mp4"), None, 10.0, config, cache
            )

        assert score == 0.8
        assert start == 1.0
        cache.save_analysis.assert_called_once()

    def test_empty_segments_returns_fallback(self):
        builder = _make_builder()

        # WHY: mock to simulate no segments found
        with patch(
            "immich_memories.analysis.unified_analyzer.UnifiedSegmentAnalyzer.analyze",
            return_value=[],
        ):
            clip = MagicMock()
            clip.duration_seconds = 8.0
            config = MagicMock()
            config.avg_clip_duration = 5.0
            cache = MagicMock()

            start, end, score = builder.run_legacy_analysis(
                clip, Path("/fake.mp4"), None, 10.0, config, cache
            )

        assert start == 0.0
        assert end == 5.0
        assert score == 0.0

    def test_clamps_end_to_video_duration(self):
        builder = _make_builder()
        seg = _make_segment(9.0, 14.0, 0.7)

        # WHY: mock to control segment positions near video end
        with patch(
            "immich_memories.analysis.unified_analyzer.UnifiedSegmentAnalyzer.analyze",
            return_value=[seg],
        ):
            clip = MagicMock()
            clip.duration_seconds = 10.0
            config = MagicMock()
            config.avg_clip_duration = 5.0
            cache = MagicMock()

            start, end, score = builder.run_legacy_analysis(
                clip, Path("/fake.mp4"), None, 10.0, config, cache
            )

        assert end <= 10.0
        assert start >= 0.0
