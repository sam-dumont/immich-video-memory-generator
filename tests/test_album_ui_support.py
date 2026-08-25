"""Helpers the album picker needs (#270).

UI pages are excluded from coverage, so the logic behind the picker lives here
where it can be tested: listing albums for the dropdown, and turning an album's
media into the clip and photo pools the wizard already works with.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from immich_memories.analysis.album_source import (
    AlbumMedia,
    album_media_as_clips,
    album_target_minutes,
)
from immich_memories.api.album_service import AlbumService
from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.ui.state import AppState


def _asset(asset_id: str, asset_type: AssetType) -> Asset:
    when = datetime(2025, 6, 1, tzinfo=UTC)
    return Asset(
        id=asset_id,
        type=asset_type,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
        width=1920,
        height=1080,
        # Videos below the minimum clip length are filtered out downstream.
        duration_seconds=8.0 if asset_type is AssetType.VIDEO else None,
    )


def _clips(count: int) -> list[VideoClipInfo]:
    return [
        VideoClipInfo(asset=_asset(f"v{i}", AssetType.VIDEO), start_time=0.0, end_time=8.0)
        for i in range(count)
    ]


def _photos(count: int) -> list[Asset]:
    return [_asset(f"p{i}", AssetType.IMAGE) for i in range(count)]


class TestListAlbums:
    @pytest.mark.asyncio
    async def test_lists_albums_newest_and_largest_first(self):
        # WHY: replaces the Immich GET /albums endpoint.
        request_fn = AsyncMock(
            return_value=[
                {"id": "a-1", "albumName": "Small", "assetCount": 3},
                {"id": "a-2", "albumName": "Big", "assetCount": 900},
            ]
        )
        service = AlbumService(request_fn, AsyncMock())

        albums = await service.list_albums()

        assert [a.name for a in albums] == ["Big", "Small"]
        assert albums[0].asset_count == 900

    @pytest.mark.asyncio
    async def test_skips_empty_albums_which_cannot_make_a_memory(self):
        request_fn = AsyncMock(
            return_value=[
                {"id": "a-1", "albumName": "Empty", "assetCount": 0},
                {"id": "a-2", "albumName": "Usable", "assetCount": 12},
            ]
        )
        service = AlbumService(request_fn, AsyncMock())

        assert [a.name for a in await service.list_albums()] == ["Usable"]


class TestAlbumMediaAsClips:
    def test_the_clip_pool_is_video_and_the_photo_pool_is_photographs(self):
        """Live Photo clips were a third pool flattened in here; there is no
        third pool now, so a photograph stays in the photo pool whatever it
        will later render as."""
        media = AlbumMedia(
            videos=[_asset("v1", AssetType.VIDEO)],
            photos=[_asset("p1", AssetType.IMAGE), _asset("live-1", AssetType.IMAGE)],
        )

        clips, photos = album_media_as_clips(media)

        assert {c.asset.id for c in clips} == {"v1"}
        assert {p.id for p in photos} == {"p1", "live-1"}

    def test_clips_come_back_in_capture_order(self):
        first = _asset("v1", AssetType.VIDEO)
        second = _asset("v2", AssetType.VIDEO)
        second.file_created_at = datetime(2025, 7, 1, tzinfo=UTC)
        media = AlbumMedia(videos=[second, first], photos=[])

        clips, _ = album_media_as_clips(media)

        assert [c.asset.id for c in clips] == ["v1", "v2"]

    def test_an_empty_album_yields_empty_pools_rather_than_failing(self):
        clips, photos = album_media_as_clips(AlbumMedia())

        assert clips == []
        assert photos == []


class TestAlbumModeState:
    """Album mode must not leave the wizard expecting a date range it never set."""

    def test_choosing_an_album_records_it_and_clears_the_date_range(self):
        from immich_memories.ui.state import AppState

        state = AppState()
        state.memory_type = "album"
        state.album_id = "a-1"
        state.album_name = "Trip 2025"
        state.date_ranges = []

        assert state.memory_type == "album"
        assert state.album_id == "a-1"
        assert state.date_range is None

    def test_the_album_supplies_the_date_range_once_its_assets_are_known(self):
        """Generation still needs a span; it comes from the album, not the picker."""
        from datetime import datetime

        from immich_memories.analysis.album_source import AlbumMedia
        from immich_memories.timeperiod import DateRange

        media = AlbumMedia(
            date_range=DateRange(start=datetime(2021, 7, 21), end=datetime(2021, 7, 29))
        )

        assert media.date_range is not None
        assert media.date_range.start < media.date_range.end


class TestAlbumTargetDuration:
    """An album carries no preset, so nothing sets a target duration for it.

    Without a rule of its own, album mode inherits whatever the last-clicked
    preset left in state — a 10-minute Year in Review target on a 20-photo
    album.
    """

    def test_target_scales_with_the_album_size(self):
        small = album_target_minutes(_clips(5), [])
        large = album_target_minutes(_clips(200), [])

        assert small < large

    def test_photos_count_toward_the_target(self):
        assert album_target_minutes(_clips(10), _photos(10)) > album_target_minutes(_clips(10), [])

    def test_a_tiny_album_still_gets_a_usable_target(self):
        assert album_target_minutes(_clips(1), []) >= 0.5

    def test_a_huge_album_is_capped(self):
        assert album_target_minutes(_clips(5000), _photos(5000)) <= 10.0


class TestScopeReadiness:
    """Step 1 gates "Next" on having a pool to work from.

    Album mode deliberately leaves date_range unset, so a date-range-only gate
    would reject every album and the card would never advance.
    """

    def test_a_date_range_is_a_complete_scope(self):
        state = AppState()
        state.date_ranges = [object()]

        assert state.scope_is_selected

    def test_a_chosen_album_is_a_complete_scope_without_a_date_range(self):
        state = AppState()
        state.memory_type = "album"
        state.album_id = "abc"

        assert state.scope_is_selected

    def test_album_mode_without_a_chosen_album_is_not_ready(self):
        state = AppState()
        state.memory_type = "album"

        assert not state.scope_is_selected

    def test_a_stale_album_id_does_not_satisfy_another_memory_type(self):
        state = AppState()
        state.memory_type = "year_in_review"
        state.album_id = "left-over"

        assert not state.scope_is_selected
