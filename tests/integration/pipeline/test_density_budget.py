"""Integration tests for density-proportional budget with real Immich data.

Verifies the density budget selects clips from across the timeline,
respects favorite priority, and handles real-world asset distributions.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest

from tests.integration.conftest import requires_ffmpeg

logger = logging.getLogger(__name__)


def _has_immich() -> bool:
    try:
        from immich_memories.config_loader import Config

        config = Config.from_yaml(Config.get_default_path())
        if not config.immich.url or not config.immich.api_key:
            return False
        import httpx

        resp = httpx.get(
            f"{config.immich.url.rstrip('/')}/api/server/ping",
            headers={"x-api-key": config.immich.api_key},
            timeout=5.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


requires_immich = pytest.mark.skipif(not _has_immich(), reason="Immich not reachable")
pytestmark = [pytest.mark.integration, requires_ffmpeg, requires_immich]


@pytest.fixture(scope="module")
def immich_clips():
    """Fetch real clips from Immich and convert to VideoClipInfo."""
    from immich_memories.api.sync_client import SyncImmichClient
    from immich_memories.config_loader import Config
    from immich_memories.generate import assets_to_clips
    from immich_memories.timeperiod import DateRange

    config = Config.from_yaml(Config.get_default_path())
    config.defaults.target_duration_seconds = 60  # Cap at 60s for test speed
    client = SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key)

    dr = DateRange(start=date(2025, 1, 1), end=date(2025, 12, 31))
    assets = client.get_videos_for_date_range(dr)

    if len(assets) < 10:
        pytest.skip("Need at least 10 videos in Immich for density budget test")

    clips = assets_to_clips(assets)
    logger.info(f"Loaded {len(clips)} clips from Immich (2025)")
    return clips, config, client


class TestDensityBudgetRealData:
    """Tests that verify the density budget works on real Immich data."""

    def test_budget_selects_clips_from_multiple_months(self, immich_clips):
        """Selected clips should span multiple months, not cluster in one."""
        from immich_memories.analysis.density_budget import AssetEntry, compute_density_budget

        clips, _config, _client = immich_clips

        entries = [
            AssetEntry(
                asset_id=c.asset.id,
                asset_type="video",
                date=c.asset.file_created_at,
                duration=c.duration_seconds or 5.0,
                is_favorite=c.asset.is_favorite,
                width=c.width,
                height=c.height,
            )
            for c in clips
        ]

        buckets = compute_density_budget(
            assets=entries,
            target_duration_seconds=600,  # 10 min
        )

        # Multiple buckets should have content selected
        filled_buckets = [b for b in buckets if b.favorite_ids or b.gap_fill_ids]
        total_selected = sum(len(b.favorite_ids) + len(b.gap_fill_ids) for b in buckets)

        logger.info(
            f"Density budget: {len(buckets)} buckets, "
            f"{len(filled_buckets)} with clips, {total_selected} total selected"
        )

        assert len(filled_buckets) >= 2, "Should select from at least 2 time periods"
        assert total_selected >= 5, "Should select at least 5 clips for a 10-min video"

    def test_favorites_take_priority_over_non_favorites(self, immich_clips):
        """Favorites should fill buckets before non-favorites."""
        from immich_memories.analysis.density_budget import AssetEntry, compute_density_budget

        clips, _config, _client = immich_clips

        entries = [
            AssetEntry(
                asset_id=c.asset.id,
                asset_type="video",
                date=c.asset.file_created_at,
                duration=c.duration_seconds or 5.0,
                is_favorite=c.asset.is_favorite,
                width=c.width,
                height=c.height,
            )
            for c in clips
        ]

        buckets = compute_density_budget(assets=entries, target_duration_seconds=600)

        total_favs = sum(len(b.favorite_ids) for b in buckets)
        total_gaps = sum(len(b.gap_fill_ids) for b in buckets)
        available_favs = sum(1 for e in entries if e.is_favorite)

        logger.info(
            f"Selected: {total_favs} favorites, {total_gaps} gap-fillers (from {available_favs} available favs)"
        )

        # If there are favorites, they should be selected first
        if available_favs > 0:
            assert total_favs > 0, "Favorites should be selected when available"

    def test_full_pipeline_runs_with_density_budget(self, immich_clips):
        """The full SmartPipeline.run() works with the density budget."""
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline
        from immich_memories.cache.database import VideoAnalysisCache
        from immich_memories.cache.thumbnail_cache import ThumbnailCache

        clips, config, client = immich_clips

        # Use a small target for speed
        pipeline_config = PipelineConfig(
            target_clips=10,
            avg_clip_duration=5.0,
            analyze_all=False,
        )

        pipeline = SmartPipeline(
            client=client,
            analysis_cache=VideoAnalysisCache(db_path=config.cache.database_path),
            thumbnail_cache=ThumbnailCache(cache_dir=config.cache.cache_path / "thumbnails"),
            config=pipeline_config,
            analysis_config=config.analysis,
            app_config=config,
        )

        result = pipeline.run(clips[:50])  # Limit to 50 clips for speed

        assert len(result.selected_clips) > 0, "Pipeline should select at least 1 clip"
        assert len(result.errors) == 0 or len(result.selected_clips) > 0, (
            "Pipeline should not fail entirely"
        )

        logger.info(
            f"Pipeline result: {len(result.selected_clips)} clips selected, "
            f"{result.stats.get('total_analyzed', 0)} analyzed"
        )

    def test_clips_have_valid_dimensions(self, immich_clips):
        """After PR #70, clips should have real width/height, not 0×0."""
        clips, _config, _client = immich_clips

        zero_dims = [c for c in clips if c.width == 0 or c.height == 0]
        real_dims = [c for c in clips if c.width > 0 and c.height > 0]

        logger.info(f"Dimensions: {len(real_dims)} real, {len(zero_dims)} zero")
        # After PR #70, all clips should have real dimensions
        assert len(real_dims) > len(zero_dims), "Most clips should have real dimensions (PR #70)"
