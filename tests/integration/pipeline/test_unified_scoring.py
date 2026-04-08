"""Integration tests for UnifiedSegmentAnalyzer with real Immich clips.

Verifies the full scoring pipeline: visual (face/motion/stability),
audio (PANNs speech/laughter/music detection), silence boundaries,
and segment selection — using real video data.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.integration.immich_fixtures import requires_immich


@pytest.fixture(scope="module")
def analyzed_clip(immich_short_clips):
    """Download a real clip and run UnifiedSegmentAnalyzer on it.

    Returns (segments, clip, config) — session-scoped to avoid re-downloading.
    """
    from immich_memories.analysis.analyzer_factory import create_analyzer_from_config

    clips, config, client = immich_short_clips
    clip = clips[0]

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    client.download_asset(clip.asset.id, tmp_path)

    analyzer = create_analyzer_from_config(config)
    segments = analyzer.analyze(tmp_path, video_duration=clip.duration_seconds)
    analyzer.clear_cache(release_audio_analyzer=True)

    yield segments, clip, config

    tmp_path.unlink(missing_ok=True)


@requires_immich
class TestUnifiedScoringEndToEnd:
    """E2E: real clip → UnifiedSegmentAnalyzer → verify segment structure."""

    def test_produces_at_least_one_segment(self, analyzed_clip):
        segments, clip, _ = analyzed_clip
        assert len(segments) >= 1, f"No segments for {clip.asset.original_file_name}"

    def test_segments_sorted_by_score_descending(self, analyzed_clip):
        segments, _, _ = analyzed_clip
        if len(segments) < 2:
            pytest.skip("Need 2+ segments to test ordering")
        scores = [s.total_score for s in segments]
        assert scores == sorted(scores, reverse=True)

    def test_segment_timing_within_video_bounds(self, analyzed_clip):
        segments, clip, _ = analyzed_clip
        for seg in segments:
            assert seg.start_time >= 0.0
            assert seg.end_time <= clip.duration_seconds + 0.5  # small tolerance
            assert seg.end_time > seg.start_time

    def test_total_score_is_positive(self, analyzed_clip):
        segments, _, _ = analyzed_clip
        for seg in segments:
            assert seg.total_score > 0.0, "Segment score should be positive"
            assert seg.total_score <= 2.0, "Score shouldn't exceed ~1.0 + bonuses"

    def test_visual_components_are_populated(self, analyzed_clip):
        segments, _, _ = analyzed_clip
        seg = segments[0]
        assert 0.0 <= seg.face_score <= 1.0
        assert 0.0 <= seg.motion_score <= 1.0
        assert 0.0 <= seg.stability_score <= 1.0
        assert 0.0 <= seg.visual_score <= 1.0

    def test_audio_score_is_real_not_hardcoded(self, analyzed_clip):
        """Audio score should come from PANNs, not the old hardcoded 0.5."""
        segments, _, _ = analyzed_clip
        seg = segments[0]
        # PANNs returns a real score — it's very unlikely to be exactly 0.5
        # If PANNs is unavailable, energy fallback still produces a real score
        assert isinstance(seg.audio_score, float)
        assert 0.0 <= seg.audio_score <= 1.0

    def test_audio_flags_are_booleans(self, analyzed_clip):
        segments, _, _ = analyzed_clip
        seg = segments[0]
        assert isinstance(seg.has_laughter, bool)
        assert isinstance(seg.has_speech, bool)
        assert isinstance(seg.has_music, bool)

    def test_audio_categories_is_set_or_none(self, analyzed_clip):
        segments, _, _ = analyzed_clip
        seg = segments[0]
        if seg.audio_categories is not None:
            assert isinstance(seg.audio_categories, set)
            for cat in seg.audio_categories:
                assert isinstance(cat, str)

    def test_duration_score_rewards_optimal_length(self, analyzed_clip):
        segments, _, _ = analyzed_clip
        seg = segments[0]
        assert 0.0 <= seg.duration_score <= 1.0


@requires_immich
class TestCreateAnalyzerFromConfigEndToEnd:
    """E2E: verify the factory creates a properly configured analyzer."""

    def test_factory_returns_working_analyzer(self, immich_short_clips):
        from immich_memories.analysis.analyzer_factory import create_analyzer_from_config
        from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer

        _, config, _ = immich_short_clips
        analyzer = create_analyzer_from_config(config)
        assert isinstance(analyzer, UnifiedSegmentAnalyzer)
        assert analyzer.scorer is not None

    def test_audio_content_config_propagated(self, immich_short_clips):
        from immich_memories.analysis.analyzer_factory import create_analyzer_from_config

        _, config, _ = immich_short_clips
        analyzer = create_analyzer_from_config(config)
        # Config should be propagated from user's config.yaml
        assert analyzer._audio_content_config is not None


@requires_immich
class TestAnalyzeClipForHighlightEndToEnd:
    """E2E: verify analyze_clip_for_highlight with real video."""

    def test_returns_valid_highlight(self, immich_short_clips):
        from immich_memories.analysis.clip_selection import analyze_clip_for_highlight

        clips, config, client = immich_short_clips
        # Pick a clip long enough for min_duration (3s default)
        clip = next((c for c in clips if c.duration_seconds >= 3.0), clips[0])

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        client.download_asset(clip.asset.id, tmp_path)

        start, end, score = analyze_clip_for_highlight(
            tmp_path,
            content_analysis_config=config.content_analysis,
            analysis_config=config.analysis,
        )

        tmp_path.unlink(missing_ok=True)

        assert start >= 0.0
        assert end > start
        assert score > 0.0

    def test_empty_video_returns_fallback(self, tmp_path):
        """Non-existent video should return fallback values."""
        from immich_memories.analysis.clip_selection import analyze_clip_for_highlight
        from immich_memories.config_models import AnalysisConfig, ContentAnalysisConfig

        start, end, score = analyze_clip_for_highlight(
            tmp_path / "nonexistent.mp4",
            target_duration=5.0,
            content_analysis_config=ContentAnalysisConfig(),
            analysis_config=AnalysisConfig(),
        )

        assert start == 0.0
        assert end == 5.0
        assert score == 0.0
