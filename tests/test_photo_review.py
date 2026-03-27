"""Tests for photo review in Step 2 clip grid (issue #191)."""

from __future__ import annotations

from datetime import UTC, datetime

from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.ui.pages.clip_grid import grid_item_date, grid_item_id
from immich_memories.ui.state import AppState


def make_photo_asset(
    asset_id: str = "photo-001",
    *,
    file_created_at: datetime | None = None,
    is_favorite: bool = False,
) -> Asset:
    """Create a photo Asset for testing."""
    now = file_created_at or datetime.now(tz=UTC)
    return Asset(
        id=asset_id,
        type=AssetType.IMAGE,
        fileCreatedAt=now,
        fileModifiedAt=now,
        updatedAt=now,
        isFavorite=is_favorite,
        originalFileName=f"{asset_id}.heic",
    )


class TestPhotoSelectionState:
    """Photo selection state management."""

    def test_selected_photo_ids_populated_from_photo_assets(self):
        """When photo_assets are set, selected_photo_ids should contain all IDs."""
        state = AppState()
        photos = [make_photo_asset("p1"), make_photo_asset("p2"), make_photo_asset("p3")]
        state.photo_assets = photos
        state.selected_photo_ids = {a.id for a in photos}
        assert state.selected_photo_ids == {"p1", "p2", "p3"}

    def test_deselecting_photo_removes_from_set(self):
        state = AppState()
        state.selected_photo_ids = {"p1", "p2", "p3"}
        state.selected_photo_ids.discard("p2")
        assert state.selected_photo_ids == {"p1", "p3"}

    def test_get_selected_photos_filters_by_ids(self):
        """Filtering photo_assets by selected_photo_ids yields correct subset."""
        state = AppState()
        photos = [make_photo_asset("p1"), make_photo_asset("p2"), make_photo_asset("p3")]
        state.photo_assets = photos
        state.selected_photo_ids = {"p1", "p3"}
        selected = [p for p in state.photo_assets if p.id in state.selected_photo_ids]
        assert len(selected) == 2
        assert {p.id for p in selected} == {"p1", "p3"}


class TestGridItemHelpers:
    """Test grid_item_date and grid_item_id with both types."""

    def test_grid_item_date_for_clip(self):
        from tests.conftest import make_clip

        dt = datetime(2024, 6, 15, tzinfo=UTC)
        clip = make_clip("v1", file_created_at=dt)
        assert grid_item_date(clip) == dt

    def test_grid_item_date_for_photo(self):
        dt = datetime(2024, 6, 15, tzinfo=UTC)
        photo = make_photo_asset("p1", file_created_at=dt)
        assert grid_item_date(photo) == dt

    def test_grid_item_id_for_clip(self):
        from tests.conftest import make_clip

        clip = make_clip("v1")
        assert grid_item_id(clip) == "v1"

    def test_grid_item_id_for_photo(self):
        photo = make_photo_asset("p1")
        assert grid_item_id(photo) == "p1"


class TestPhotoGridItemSorting:
    """Chronological interleaving of photos and video clips."""

    def test_mixed_items_sort_by_date(self):
        """Photos and video clips sort together by file_created_at."""
        from tests.conftest import make_clip

        early = datetime(2024, 1, 1, tzinfo=UTC)
        mid = datetime(2024, 6, 15, tzinfo=UTC)
        late = datetime(2024, 12, 31, tzinfo=UTC)

        clip1 = make_clip("v1", file_created_at=early)
        photo1 = make_photo_asset("p1", file_created_at=mid)
        clip2 = make_clip("v2", file_created_at=late)

        items: list[VideoClipInfo | Asset] = [clip2, photo1, clip1]
        sorted_items = sorted(items, key=grid_item_date)

        assert isinstance(sorted_items[0], VideoClipInfo)
        assert sorted_items[0].asset.id == "v1"
        assert isinstance(sorted_items[1], Asset)
        assert sorted_items[1].id == "p1"
        assert isinstance(sorted_items[2], VideoClipInfo)
        assert sorted_items[2].asset.id == "v2"


class TestBuildMixedItems:
    """Test _build_mixed_items helper for merging clips and photos."""

    def test_no_photos_returns_clips_only(self):
        from immich_memories.ui.pages.step2_review import _build_mixed_items
        from tests.conftest import make_clip

        state = AppState()
        clips = [make_clip("v1")]
        state.include_photos = False
        result = _build_mixed_items(clips, state)
        assert len(result) == 1
        assert isinstance(result[0], VideoClipInfo)

    def test_with_photos_returns_sorted_mix(self):
        from immich_memories.ui.pages.step2_review import _build_mixed_items
        from tests.conftest import make_clip

        early = datetime(2024, 1, 1, tzinfo=UTC)
        late = datetime(2024, 12, 31, tzinfo=UTC)

        state = AppState()
        state.include_photos = True
        state.photo_assets = [make_photo_asset("p1", file_created_at=late)]
        clips = [make_clip("v1", file_created_at=early)]
        result = _build_mixed_items(clips, state)

        assert len(result) == 2
        assert isinstance(result[0], VideoClipInfo)
        assert isinstance(result[1], Asset)


class TestBuildHeaderLabel:
    """Test header label shows both video and photo counts."""

    def test_videos_only(self):
        from immich_memories.ui.pages.step2_review import _build_header_label
        from tests.conftest import make_clip

        state = AppState()
        state.include_photos = False
        clips = [make_clip("v1"), make_clip("v2")]
        assert _build_header_label(clips, state) == "2 Videos Found"

    def test_videos_and_photos(self):
        from immich_memories.ui.pages.step2_review import _build_header_label
        from tests.conftest import make_clip

        state = AppState()
        state.include_photos = True
        state.photo_assets = [make_photo_asset("p1"), make_photo_asset("p2")]
        clips = [make_clip("v1")]
        assert _build_header_label(clips, state) == "1 Videos, 2 Photos Found"


class TestFilterSelectedPhotos:
    """Photos filtered by selected_photo_ids before passing to generation."""

    def test_only_selected_photos_passed(self):
        from immich_memories.ui.pages._step4_generate import _filter_selected_photos

        state = AppState()
        photos = [make_photo_asset("p1"), make_photo_asset("p2"), make_photo_asset("p3")]
        state.photo_assets = photos
        state.selected_photo_ids = {"p1", "p3"}
        state.include_photos = True

        filtered = _filter_selected_photos(state)
        assert filtered is not None
        assert len(filtered) == 2
        assert {p.id for p in filtered} == {"p1", "p3"}

    def test_empty_selection_yields_empty_list(self):
        from immich_memories.ui.pages._step4_generate import _filter_selected_photos

        state = AppState()
        state.photo_assets = [make_photo_asset("p1")]
        state.selected_photo_ids = set()
        state.include_photos = True

        filtered = _filter_selected_photos(state)
        assert filtered is not None
        assert filtered == []

    def test_photos_disabled_yields_none(self):
        from immich_memories.ui.pages._step4_generate import _filter_selected_photos

        state = AppState()
        state.photo_assets = [make_photo_asset("p1")]
        state.include_photos = False

        result = _filter_selected_photos(state)
        assert result is None

    def test_no_photo_assets_yields_none(self):
        from immich_memories.ui.pages._step4_generate import _filter_selected_photos

        state = AppState()
        state.include_photos = True
        state.photo_assets = []

        result = _filter_selected_photos(state)
        assert result is None
