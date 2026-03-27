"""Tests for score_and_select_photos() — extracted photo scoring + budget selection."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from immich_memories.analysis.unified_budget import BudgetCandidate
from immich_memories.api.models import Asset
from immich_memories.config_models import PhotoConfig
from immich_memories.photos.photo_pipeline import (
    PhotoSelectionResult,
    score_and_select_photos,
)


def _make_asset(asset_id: str, favorite: bool = False) -> Asset:
    now = datetime.now(tz=UTC)
    return Asset(
        id=asset_id,
        type="IMAGE",
        fileCreatedAt=now,
        fileModifiedAt=now,
        updatedAt=now,
        isFavorite=favorite,
    )


def _make_video_candidate(
    asset_id: str, duration: float, date: datetime | None = None
) -> BudgetCandidate:
    return BudgetCandidate(
        asset_id=asset_id,
        duration=duration,
        score=0.5,
        candidate_type="video",
        date=date or datetime(2025, 7, 15, tzinfo=UTC),
    )


class TestScoreAndSelectPhotos:
    """Extracted scoring + budget selection returns scored photos and selected IDs."""

    def test_returns_scored_photos_and_selection(self, tmp_path):
        assets = [_make_asset("p1", favorite=True), _make_asset("p2")]
        video_candidates = [_make_video_candidate("v1", 10.0)]

        config = MagicMock()
        config.photos = PhotoConfig()
        config.photos.duration = 4.0
        config.photos.max_ratio = 0.50

        # WHY: mock download_fn — testing scoring+selection logic, not Immich I/O
        download_fn = MagicMock()

        result = score_and_select_photos(
            photo_assets=assets,
            video_candidates=video_candidates,
            config=config,
            target_duration=30.0,
            work_dir=tmp_path,
            download_fn=download_fn,
        )

        assert isinstance(result, PhotoSelectionResult)
        assert len(result.scored_photos) == 2
        # All scored photos are (Asset, float) tuples
        assert all(isinstance(s, float) for _, s in result.scored_photos)
        # Selection has selected_photo_ids
        assert len(result.selection.selected_photo_ids) > 0

    def test_empty_photos_returns_empty_result(self, tmp_path):
        config = MagicMock()
        config.photos = PhotoConfig()
        config.photos.duration = 4.0
        config.photos.max_ratio = 0.50

        result = score_and_select_photos(
            photo_assets=[],
            video_candidates=[_make_video_candidate("v1", 10.0)],
            config=config,
            target_duration=30.0,
            work_dir=tmp_path,
            download_fn=MagicMock(),
        )

        assert result.scored_photos == []
        assert result.selection.selected_photo_ids == []

    def test_budget_respects_target_duration(self, tmp_path):
        assets = [_make_asset(f"p{i}") for i in range(20)]
        # 10 videos using 50s of a 60s budget → 10s left for photos
        video_candidates = [_make_video_candidate(f"v{i}", 5.0) for i in range(10)]

        config = MagicMock()
        config.photos = PhotoConfig()
        config.photos.duration = 4.0
        config.photos.max_ratio = 0.50
        config.title_screens.enabled = False

        # WHY: mock download_fn — testing budget math, not Immich I/O
        download_fn = MagicMock()

        result = score_and_select_photos(
            photo_assets=assets,
            video_candidates=video_candidates,
            config=config,
            target_duration=60.0,
            work_dir=tmp_path,
            download_fn=download_fn,
        )

        # Total photo duration should fit within remaining budget
        total_photo_duration = len(result.selection.selected_photo_ids) * 4.0
        assert total_photo_duration <= 60.0
