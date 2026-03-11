"""Integration tests for the SmartPipeline end-to-end flow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.analysis.smart_pipeline import (
    ClipWithSegment,
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


def _fake_analyze(pipeline: SmartPipeline, clips: list) -> list[ClipWithSegment]:
    """Return ClipWithSegment for each clip (simulates analysis)."""
    return [
        ClipWithSegment(clip=clip, start_time=0.0, end_time=5.0, score=0.5 + i * 0.01)
        for i, clip in enumerate(clips)
    ]


class TestSmartPipelineIntegration:
    """End-to-end tests for SmartPipeline."""

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

    @patch(
        "immich_memories.analysis.duplicates.deduplicate_by_thumbnails",
        side_effect=lambda **kw: kw["clips"],
    )
    def test_full_run_returns_pipeline_result(
        self,
        _mock_dedup,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Full 4-phase run with synthetic clips returns a PipelineResult."""
        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, avg_clip_duration=5.0, analyze_all=True),
        )
        clips = _make_clips(10, is_favorite=True)

        with patch.object(
            pipeline, "_phase_analyze", side_effect=lambda c: _fake_analyze(pipeline, c)
        ):
            result = pipeline.run(clips)

        assert isinstance(result, PipelineResult)
        assert len(result.selected_clips) > 0
        assert len(result.clip_segments) == len(result.selected_clips)
        assert isinstance(result.stats, dict)
        assert "selected_count" in result.stats

    @patch(
        "immich_memories.analysis.duplicates.deduplicate_by_thumbnails",
        side_effect=lambda **kw: kw["clips"],
    )
    def test_empty_clips_returns_empty_result(
        self,
        _mock_dedup,
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

        with patch.object(pipeline, "_phase_analyze", return_value=[]):
            result = pipeline.run([])

        assert isinstance(result, PipelineResult)
        assert result.selected_clips == []
        assert result.clip_segments == {}
        assert result.errors == []

    @patch(
        "immich_memories.analysis.duplicates.deduplicate_by_thumbnails",
        side_effect=lambda **kw: kw["clips"],
    )
    def test_hdr_only_filters_sdr_clips(
        self,
        _mock_dedup,
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
        # Differentiate IDs
        for i, c in enumerate(sdr_clips):
            c.asset.id = f"sdr-{i:03d}"

        all_clips = hdr_clips + sdr_clips

        with patch.object(
            pipeline, "_phase_analyze", side_effect=lambda c: _fake_analyze(pipeline, c)
        ):
            result = pipeline.run(all_clips)

        # SDR non-favorites should be filtered out in phase 2
        sdr_ids = {c.asset.id for c in sdr_clips}
        selected_ids = {c.asset.id for c in result.selected_clips}
        assert sdr_ids.isdisjoint(selected_ids), "SDR clips should not appear in HDR-only results"

    @patch(
        "immich_memories.analysis.duplicates.deduplicate_by_thumbnails",
        side_effect=lambda **kw: kw["clips"],
    )
    def test_progress_callback_invoked(
        self,
        _mock_dedup,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Progress callback is called during pipeline execution."""
        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, analyze_all=True),
        )
        clips = _make_clips(5, is_favorite=True)
        callback = MagicMock()

        with patch.object(
            pipeline, "_phase_analyze", side_effect=lambda c: _fake_analyze(pipeline, c)
        ):
            pipeline.run(clips, progress_callback=callback)

        assert callback.call_count >= 1, "Progress callback should be called at least once"

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

    @patch(
        "immich_memories.analysis.duplicates.deduplicate_by_thumbnails",
        side_effect=lambda **kw: kw["clips"],
    )
    def test_analyze_all_sends_all_clips(
        self,
        _mock_dedup,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """analyze_all mode sends all clips to analysis phase."""
        config = PipelineConfig(target_clips=5, analyze_all=True)
        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=config,
        )
        clips = _make_clips(8, is_favorite=False)

        analyze_calls: list = []

        def capture_analyze(c: list) -> list[ClipWithSegment]:
            analyze_calls.append(len(c))
            return _fake_analyze(pipeline, c)

        with patch.object(pipeline, "_phase_analyze", side_effect=capture_analyze):
            pipeline.run(clips)

        # All 8 clips should reach analysis (minus any < min duration)
        assert analyze_calls[0] == 8
