"""Unit tests for clip refiner — photo cap scarcity, interleaving."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.api.models import Asset, AssetType, VideoClipInfo


def _make_clip(
    asset_id: str,
    date: datetime,
    score: float = 0.5,
    duration: float = 5.0,
    is_favorite: bool = False,
    asset_type: AssetType = AssetType.VIDEO,
) -> ClipWithSegment:
    asset = Asset(
        id=asset_id,
        type=asset_type,
        fileCreatedAt=date,
        fileModifiedAt=date,
        updatedAt=date,
        isFavorite=is_favorite,
    )
    clip = VideoClipInfo(
        asset=asset,
        duration_seconds=duration,
        width=1920,
        height=1080,
    )
    return ClipWithSegment(
        clip=clip,
        start_time=0.0,
        end_time=duration,
        score=score,
    )


class TestPhotoCapScarcity:
    """Photo cap should respect video scarcity — let photos fill when needed."""

    def test_cap_enforced_when_videos_plentiful(self):
        from immich_memories.analysis.clip_refiner import enforce_photo_cap

        base = datetime(2021, 7, 22, tzinfo=UTC)
        clips = [
            _make_clip(f"v{i}", base + timedelta(hours=i), asset_type=AssetType.VIDEO, score=0.5)
            for i in range(6)
        ] + [
            _make_clip(
                f"p{i}", base + timedelta(hours=6 + i), asset_type=AssetType.IMAGE, score=0.3
            )
            for i in range(6)
        ]
        result = enforce_photo_cap(clips, max_ratio=0.40, videos_scarce=False)
        photos = [c for c in result if c.clip.asset.type == AssetType.IMAGE]
        assert len(photos) <= int(len(result) * 0.40) + 1

    def test_cap_skipped_when_videos_scarce(self):
        from immich_memories.analysis.clip_refiner import enforce_photo_cap

        base = datetime(2021, 7, 22, tzinfo=UTC)
        clips = [
            _make_clip("v1", base, asset_type=AssetType.VIDEO, score=0.5),
        ] + [
            _make_clip(
                f"p{i}", base + timedelta(hours=i + 1), asset_type=AssetType.IMAGE, score=0.3
            )
            for i in range(8)
        ]
        # With videos_scarce=True, all photos should be kept
        result = enforce_photo_cap(clips, max_ratio=0.40, videos_scarce=True)
        assert len(result) == 9

    def test_no_videos_all_photos_kept(self):
        """All photos, no videos — nothing to cap against."""
        from immich_memories.analysis.clip_refiner import enforce_photo_cap

        base = datetime(2021, 7, 22, tzinfo=UTC)
        clips = [
            _make_clip(f"p{i}", base + timedelta(hours=i), asset_type=AssetType.IMAGE, score=0.3)
            for i in range(5)
        ]
        result = enforce_photo_cap(clips, max_ratio=0.40, videos_scarce=False)
        assert len(result) == 5
