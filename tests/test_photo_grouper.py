"""Tests for photo grouping and series detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.photos.grouper import PhotoGrouper
from tests.conftest import make_asset


def _make_photo(
    asset_id: str = "photo-1",
    *,
    file_created_at: datetime | None = None,
    is_favorite: bool = False,
    exif_make: str | None = "Apple",
):
    """Create a photo Asset for testing."""
    now = file_created_at or datetime.now(tz=UTC)
    return make_asset(
        asset_id,
        file_created_at=now,
        is_favorite=is_favorite,
        exif_make=exif_make,
        duration=None,
    )


class TestTemporalClustering:
    """Tests for grouping consecutive photos by time gap."""

    def test_single_photo_returns_single_group(self):
        """One photo produces one group with one asset."""
        grouper = PhotoGrouper()
        photos = [_make_photo("p1")]
        groups = grouper.group(photos)
        assert len(groups) == 1
        assert groups[0].asset_ids == ["p1"]
        assert groups[0].is_series is False

    def test_consecutive_photos_within_gap_form_series(self):
        """Photos within series_gap_seconds are grouped together."""
        grouper = PhotoGrouper(series_gap_seconds=60.0)
        base = datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)
        photos = [
            _make_photo("p1", file_created_at=base),
            _make_photo("p2", file_created_at=base + timedelta(seconds=30)),
            _make_photo("p3", file_created_at=base + timedelta(seconds=50)),
        ]
        groups = grouper.group(photos)
        assert len(groups) == 1
        assert groups[0].asset_ids == ["p1", "p2", "p3"]
        assert groups[0].is_series is True

    def test_gap_exceeding_threshold_splits_groups(self):
        """Photos with a gap > series_gap_seconds become separate groups."""
        grouper = PhotoGrouper(series_gap_seconds=60.0)
        base = datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)
        photos = [
            _make_photo("p1", file_created_at=base),
            _make_photo("p2", file_created_at=base + timedelta(seconds=30)),
            # Big gap
            _make_photo("p3", file_created_at=base + timedelta(seconds=200)),
            _make_photo("p4", file_created_at=base + timedelta(seconds=220)),
        ]
        groups = grouper.group(photos)
        assert len(groups) == 2
        assert groups[0].asset_ids == ["p1", "p2"]
        assert groups[1].asset_ids == ["p3", "p4"]

    def test_empty_list_returns_empty(self):
        """Empty input produces empty output."""
        grouper = PhotoGrouper()
        assert grouper.group([]) == []

    def test_large_series_subsampled_to_4(self):
        """Series with >4 photos are subsampled to at most 4."""
        grouper = PhotoGrouper(series_gap_seconds=60.0)
        base = datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)
        photos = [
            _make_photo(f"p{i}", file_created_at=base + timedelta(seconds=i * 10)) for i in range(8)
        ]
        groups = grouper.group(photos)
        # Each group should have at most 4 photos
        for group in groups:
            assert len(group.asset_ids) <= 4
