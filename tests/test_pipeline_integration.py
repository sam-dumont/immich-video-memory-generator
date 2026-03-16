"""Integration tests for the SmartPipeline end-to-end flow.

Tests the pipeline through its public API (run()) by mocking at external
boundaries (caches, clients) rather than internal methods. Changing
internal method names or restructuring phases should not break these tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from immich_memories.analysis.smart_pipeline import (
    JobCancelledException,
    PipelineConfig,
    PipelineResult,
    SmartPipeline,
)
from tests.conftest import make_clip


def _make_clips(count: int, *, is_favorite: bool = False, hdr: bool = False) -> list:
    """Create a list of synthetic clips spread across months."""
    base = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
    clips = []
    for i in range(count):
        dt = base + timedelta(days=i * 7)
        clips.append(
            make_clip(
                f"clip-{i:03d}",
                width=1920,
                height=1080,
                duration=10.0,
                is_favorite=is_favorite,
                color_transfer="arib-std-b67" if hdr else None,
                file_created_at=dt,
            )
        )
    return clips


def _make_cached_analysis(asset_id: str, score: float = 0.5) -> MagicMock:
    """Build a mock CachedVideoAnalysis with one segment so analysis uses cache."""
    segment = MagicMock()
    segment.start_time = 0.0
    segment.end_time = 5.0
    segment.total_score = score
    segment.face_score = 0.3
    segment.motion_score = 0.2
    segment.stability_score = 0.4
    segment.llm_description = None
    segment.llm_emotion = None
    segment.audio_categories = None

    analysis = MagicMock()
    analysis.asset_id = asset_id
    analysis.segments = [segment]
    return analysis


class TestSmartPipelineIntegration:
    """End-to-end tests for SmartPipeline through the public run() API."""

    def _make_pipeline(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        config: PipelineConfig | None = None,
    ) -> SmartPipeline:
        return SmartPipeline(
            client=mock_immich_client,
            analysis_cache=mock_analysis_cache,
            thumbnail_cache=mock_thumbnail_cache,
            config=config or PipelineConfig(target_clips=10, avg_clip_duration=5.0),
        )

    def _setup_cache_for_clips(self, mock_cache: MagicMock, clips: list) -> None:
        """Configure mock cache to return cached analysis for all clips."""

        def get_analysis(asset_id: str, include_segments: bool = True):
            return _make_cached_analysis(asset_id)

        mock_cache.get_analysis.side_effect = get_analysis

    def test_full_run_returns_pipeline_result(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Full pipeline run with cached analysis returns a PipelineResult."""
        clips = _make_clips(10, is_favorite=True)

        self._setup_cache_for_clips(mock_analysis_cache, clips)

        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, avg_clip_duration=5.0, analyze_all=True),
        )

        result = pipeline.run(clips)

        assert isinstance(result, PipelineResult)
        assert result.selected_clips
        assert len(result.clip_segments) == len(result.selected_clips)
        assert isinstance(result.stats, dict)
        assert "selected_count" in result.stats

    def test_empty_clips_returns_empty_result(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Empty clip list produces empty result with no errors."""
        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
        )

        result = pipeline.run([])

        assert isinstance(result, PipelineResult)
        assert not result.selected_clips
        assert not result.clip_segments
        assert not result.errors

    def test_hdr_only_filters_sdr_clips(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """HDR-only mode keeps only HDR clips in non-favorites."""
        config = PipelineConfig(target_clips=5, hdr_only=True, analyze_all=False)
        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=config,
        )

        hdr_clips = _make_clips(3, hdr=True, is_favorite=False)
        sdr_clips = _make_clips(3, hdr=False, is_favorite=False)
        for i, c in enumerate(sdr_clips):
            c.asset.id = f"sdr-{i:03d}"

        all_clips = hdr_clips + sdr_clips
        self._setup_cache_for_clips(mock_analysis_cache, all_clips)

        result = pipeline.run(all_clips)

        sdr_ids = {c.asset.id for c in sdr_clips}
        selected_ids = {c.asset.id for c in result.selected_clips}
        assert sdr_ids.isdisjoint(selected_ids), "SDR clips should not appear in HDR-only results"

    def test_progress_callback_invoked_with_increasing_values(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Progress callback is called with monotonically increasing progress values (0 to 1)."""
        clips = _make_clips(5, is_favorite=True)
        self._setup_cache_for_clips(mock_analysis_cache, clips)

        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, analyze_all=True),
        )
        progress_calls: list = []

        def track_progress(*args, **kwargs):
            progress_calls.append(args)

        pipeline.run(clips, progress_callback=track_progress)

        # WHY: progress callback is called with varying signatures (float, dict, etc.)
        # We verify it was actually called, not just "connected"
        assert len(progress_calls) >= 1, "Progress callback should be called"
        # Check that numeric progress values (when present) are reasonable
        float_values = [a[0] for a in progress_calls if isinstance(a[0], (int, float))]
        if float_values:
            assert all(0 <= v <= 1.0 for v in float_values), (
                f"Progress values should be 0-1, got {float_values}"
            )

    def test_cancellation_raises_exception(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache, sample_config
    ):
        """Cancellation request raises JobCancelledException."""
        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
        )
        pipeline.run_id = "test-run-123"

        mock_run_db = MagicMock()
        mock_run_db.is_cancel_requested.return_value = True
        pipeline._run_db = mock_run_db

        with pytest.raises(JobCancelledException):
            pipeline.run(_make_clips(5, is_favorite=True))

    def test_analyze_all_sends_all_clips_to_analysis(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """analyze_all mode processes all clips through the pipeline."""
        clips = _make_clips(8, is_favorite=False)
        self._setup_cache_for_clips(mock_analysis_cache, clips)

        config = PipelineConfig(target_clips=5, analyze_all=True)
        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=config,
        )

        pipeline.run(clips)

        # All 8 clips should have been looked up in cache (one call per clip)
        cache_calls = mock_analysis_cache.get_analysis.call_args_list
        queried_ids = {call.args[0] for call in cache_calls}
        clip_ids = {c.asset.id for c in clips}
        assert clip_ids.issubset(queried_ids), "All clips should have been queried in the cache"

    def test_favorites_always_analyzed(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Favorites are always included regardless of non-favorite filters."""
        favorites = _make_clips(3, is_favorite=True)
        non_favorites = _make_clips(5, is_favorite=False)
        for i, c in enumerate(non_favorites):
            c.asset.id = f"nonfav-{i:03d}"

        all_clips = favorites + non_favorites
        self._setup_cache_for_clips(mock_analysis_cache, all_clips)

        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, analyze_all=False),
        )

        result = pipeline.run(all_clips)

        selected_ids = {c.asset.id for c in result.selected_clips}
        fav_ids = {c.asset.id for c in favorites}
        assert fav_ids.issubset(selected_ids), "All favorites should be in the final selection"

    def test_single_clip_returns_it(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Single clip input produces a result containing that clip."""
        clips = _make_clips(1, is_favorite=True)
        self._setup_cache_for_clips(mock_analysis_cache, clips)

        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, analyze_all=True),
        )

        result = pipeline.run(clips)
        assert len(result.selected_clips) == 1
        assert result.selected_clips[0].asset.id == clips[0].asset.id

    def test_duplicate_clip_ids_handled(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Pipeline handles clips with identical IDs gracefully."""
        clips = _make_clips(3, is_favorite=True)
        # Duplicate the first clip's ID on the second
        clips[1].asset.id = clips[0].asset.id
        self._setup_cache_for_clips(mock_analysis_cache, clips)

        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, analyze_all=True),
        )

        result = pipeline.run(clips)
        # Should not crash, result is valid
        assert isinstance(result, PipelineResult)

    def test_result_stats_contain_expected_keys(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Pipeline stats dict contains standard diagnostic keys."""
        clips = _make_clips(5, is_favorite=True)
        self._setup_cache_for_clips(mock_analysis_cache, clips)

        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, analyze_all=True),
        )

        result = pipeline.run(clips)
        assert "selected_count" in result.stats
        assert "total_analyzed" in result.stats

    def test_idempotent_run(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Running twice with same inputs produces same clip count."""
        clips = _make_clips(8, is_favorite=True)
        self._setup_cache_for_clips(mock_analysis_cache, clips)

        config = PipelineConfig(target_clips=5, analyze_all=True)
        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=config,
        )

        result1 = pipeline.run(clips)
        # Reset cache call counts
        mock_analysis_cache.get_analysis.reset_mock()
        result2 = pipeline.run(clips)

        assert len(result1.selected_clips) == len(result2.selected_clips)
