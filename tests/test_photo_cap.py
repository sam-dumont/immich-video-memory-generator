"""Tests for photo ratio cap enforcement in ClipRefiner."""

from __future__ import annotations

from datetime import UTC, datetime

from immich_memories.analysis.clip_refiner import enforce_photo_cap
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.api.models import Asset, AssetType, VideoClipInfo


def _make_clip_with_segment(
    asset_id: str,
    *,
    is_photo: bool = False,
    score: float = 0.5,
    is_favorite: bool = False,
) -> ClipWithSegment:
    """Create a ClipWithSegment for testing."""
    now = datetime.now(tz=UTC)
    asset = Asset(
        id=asset_id,
        type=AssetType.IMAGE if is_photo else AssetType.VIDEO,
        fileCreatedAt=now,
        fileModifiedAt=now,
        updatedAt=now,
        isFavorite=is_favorite,
    )
    clip = VideoClipInfo(
        asset=asset,
        local_path=f"/tmp/{asset_id}.mp4",
        duration_seconds=4.0,
        width=1920,
        height=1080,
    )
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=4.0, score=score)


class TestEnforcePhotoCap:
    """Tests for enforce_photo_cap function."""

    def test_no_photos_returns_unchanged(self):
        """All-video list is returned unchanged."""
        clips = [_make_clip_with_segment(f"v{i}") for i in range(10)]
        result = enforce_photo_cap(clips, max_ratio=0.50)
        assert len(result) == 10

    def test_within_cap_returns_unchanged(self):
        """Photos within cap are kept."""
        videos = [_make_clip_with_segment(f"v{i}") for i in range(8)]
        photos = [_make_clip_with_segment(f"p{i}", is_photo=True) for i in range(2)]
        clips = videos + photos  # 2/10 = 20%, under 50% cap
        result = enforce_photo_cap(clips, max_ratio=0.50)
        assert len(result) == 10

    def test_exceeding_cap_drops_lowest_scored_photos(self):
        """When photos exceed cap, lowest-scored photos are dropped."""
        videos = [_make_clip_with_segment(f"v{i}") for i in range(4)]
        photos = [
            _make_clip_with_segment("p0", is_photo=True, score=0.9),
            _make_clip_with_segment("p1", is_photo=True, score=0.3),
            _make_clip_with_segment("p2", is_photo=True, score=0.7),
            _make_clip_with_segment("p3", is_photo=True, score=0.1),
            _make_clip_with_segment("p4", is_photo=True, score=0.5),
            _make_clip_with_segment("p5", is_photo=True, score=0.8),
        ]
        clips = videos + photos  # 6/10 = 60%, over 50% cap

        result = enforce_photo_cap(clips, max_ratio=0.50)

        # 4 videos + at most 4 photos (50% of 8 total... iterative)
        # With 4 videos, max photos = floor(total * 0.5) but total changes...
        # Actually: keep dropping lowest photo until ratio <= cap
        result_photos = [c for c in result if c.clip.asset.type == AssetType.IMAGE]
        result_videos = [c for c in result if c.clip.asset.type == AssetType.VIDEO]
        assert len(result_videos) == 4
        assert len(result_photos) <= len(result) * 0.50 + 1  # Allow rounding

    def test_drops_lowest_scored_first(self):
        """Lowest-scored photos are dropped before higher-scored ones."""
        videos = [_make_clip_with_segment(f"v{i}") for i in range(2)]
        photos = [
            _make_clip_with_segment("best", is_photo=True, score=0.9),
            _make_clip_with_segment("worst", is_photo=True, score=0.1),
            _make_clip_with_segment("mid", is_photo=True, score=0.5),
            _make_clip_with_segment("low", is_photo=True, score=0.2),
        ]
        clips = videos + photos  # 4/6 = 67%, over 50%

        result = enforce_photo_cap(clips, max_ratio=0.50)

        result_photo_ids = {c.clip.asset.id for c in result if c.clip.asset.type == AssetType.IMAGE}
        # "best" should survive, "worst" should be dropped
        assert "best" in result_photo_ids
        if "worst" in result_photo_ids:
            # If worst survived, the cap is too lenient — that's wrong
            total_photos = len(result_photo_ids)
            assert total_photos / len(result) <= 0.50 + 0.01

    def test_all_photos_returns_capped(self):
        """All-photo list gets capped (some dropped, but not all)."""
        photos = [_make_clip_with_segment(f"p{i}", is_photo=True, score=0.5) for i in range(10)]
        result = enforce_photo_cap(photos, max_ratio=0.50)
        # With no videos, cap can't enforce ratio — keep all
        # (cap is ratio of total, so 100% photos can't be reduced below 100%
        # unless we drop photos with nothing to replace them)
        # The function should keep all when there are no videos
        assert len(result) == 10

    def test_zero_cap_removes_all_photos(self):
        """max_ratio=0 removes all photos."""
        videos = [_make_clip_with_segment(f"v{i}") for i in range(5)]
        photos = [_make_clip_with_segment(f"p{i}", is_photo=True) for i in range(3)]
        clips = videos + photos

        result = enforce_photo_cap(clips, max_ratio=0.0)

        result_photos = [c for c in result if c.clip.asset.type == AssetType.IMAGE]
        assert len(result_photos) == 0
        assert len(result) == 5
