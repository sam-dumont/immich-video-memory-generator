"""Tests for sparse content adaptations — adaptive target and thorough LLM threshold."""

from __future__ import annotations

from datetime import UTC, datetime

from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline
from immich_memories.api.models import Asset, AssetType, VideoClipInfo


def _make_clip(
    asset_id: str,
    date: datetime,
    duration: float = 10.0,
    is_favorite: bool = False,
    width: int = 1920,
    height: int = 1080,
) -> VideoClipInfo:
    asset = Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=date,
        fileModifiedAt=date,
        updatedAt=date,
        isFavorite=is_favorite,
        width=width,
        height=height,
    )
    return VideoClipInfo(
        asset=asset,
        duration_seconds=duration,
        width=width,
        height=height,
        bitrate=10_000_000,
    )


class TestAdaptiveTarget:
    """Target clip count should adapt when content is sparse."""

    def test_sparse_content_reduces_target(self):
        """8 clips available but target is 120 → target should reduce."""
        config = PipelineConfig(target_clips=120, avg_clip_duration=5.0)
        pipeline = SmartPipeline.__new__(SmartPipeline)
        pipeline.config = config

        clips = [_make_clip(f"c{i}", datetime(2021, 7, 22 + i, tzinfo=UTC)) for i in range(8)]

        pipeline._adapt_target_for_content(clips)

        assert config.target_clips == 8

    def test_dense_content_keeps_target(self):
        """200 clips available with target 120 → target stays at 120."""
        config = PipelineConfig(target_clips=120, avg_clip_duration=5.0)
        pipeline = SmartPipeline.__new__(SmartPipeline)
        pipeline.config = config

        clips = [_make_clip(f"c{i}", datetime(2021, 1, 1, tzinfo=UTC)) for i in range(200)]

        pipeline._adapt_target_for_content(clips)

        assert config.target_clips == 120

    def test_very_sparse_floors_at_5(self):
        """3 clips available → target should floor at 5, not 3."""
        config = PipelineConfig(target_clips=120, avg_clip_duration=5.0)
        pipeline = SmartPipeline.__new__(SmartPipeline)
        pipeline.config = config

        clips = [_make_clip(f"c{i}", datetime(2021, 7, 22 + i, tzinfo=UTC)) for i in range(3)]

        pipeline._adapt_target_for_content(clips)

        assert config.target_clips == 5

    def test_exactly_half_of_target_no_change(self):
        """60 clips with target 120 → exactly 50%, no change."""
        config = PipelineConfig(target_clips=120, avg_clip_duration=5.0)
        pipeline = SmartPipeline.__new__(SmartPipeline)
        pipeline.config = config

        clips = [_make_clip(f"c{i}", datetime(2021, 7, 1, tzinfo=UTC)) for i in range(60)]

        pipeline._adapt_target_for_content(clips)

        assert config.target_clips == 120


class TestThoroughLLMThreshold:
    """Switch to thorough analysis when favorites are too few to drive selection."""

    def test_zero_favorites_switches_to_thorough(self):
        """0 favorites + fast mode → should switch to thorough."""
        config = PipelineConfig(target_clips=10, analysis_depth="fast")
        pipeline = SmartPipeline.__new__(SmartPipeline)
        pipeline.config = config

        clips = [
            _make_clip(f"c{i}", datetime(2021, 7, 22 + i, tzinfo=UTC), is_favorite=False)
            for i in range(8)
        ]

        pipeline._maybe_switch_to_thorough(clips)

        assert config.analysis_depth == "thorough"

    def test_three_favorites_switches_to_thorough(self):
        """3 favorites < threshold of 5 → should switch to thorough."""
        config = PipelineConfig(target_clips=20, analysis_depth="fast")
        pipeline = SmartPipeline.__new__(SmartPipeline)
        pipeline.config = config

        clips = [
            _make_clip(f"fav{i}", datetime(2021, 7, 1 + i, tzinfo=UTC), is_favorite=True)
            for i in range(3)
        ] + [
            _make_clip(f"nf{i}", datetime(2021, 8, 1 + i, tzinfo=UTC), is_favorite=False)
            for i in range(10)
        ]

        pipeline._maybe_switch_to_thorough(clips)

        assert config.analysis_depth == "thorough"

    def test_ten_favorites_stays_fast(self):
        """10 favorites >= threshold → should stay fast."""
        config = PipelineConfig(target_clips=20, analysis_depth="fast")
        pipeline = SmartPipeline.__new__(SmartPipeline)
        pipeline.config = config

        clips = [
            _make_clip(f"fav{i}", datetime(2021, 7, 1 + i, tzinfo=UTC), is_favorite=True)
            for i in range(10)
        ] + [
            _make_clip(f"nf{i}", datetime(2021, 8, 1 + i, tzinfo=UTC), is_favorite=False)
            for i in range(20)
        ]

        pipeline._maybe_switch_to_thorough(clips)

        assert config.analysis_depth == "fast"

    def test_already_thorough_no_change(self):
        """Already thorough mode → stays thorough regardless of favorites."""
        config = PipelineConfig(target_clips=10, analysis_depth="thorough")
        pipeline = SmartPipeline.__new__(SmartPipeline)
        pipeline.config = config

        clips = [
            _make_clip(f"fav{i}", datetime(2021, 7, 1 + i, tzinfo=UTC), is_favorite=True)
            for i in range(20)
        ]

        pipeline._maybe_switch_to_thorough(clips)

        assert config.analysis_depth == "thorough"
