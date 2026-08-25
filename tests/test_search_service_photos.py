"""Tests for photo search in SearchService."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from immich_memories.api.models import Asset, AssetType, MetadataSearchResult
from immich_memories.api.search_service import SearchService
from immich_memories.timeperiod import DateRange


def _make_image_asset(asset_id: str, *, is_live_photo: bool = False) -> Asset:
    """Create an IMAGE asset for testing."""
    now = datetime.now(tz=UTC)
    return Asset(
        id=asset_id,
        type=AssetType.IMAGE,
        fileCreatedAt=now,
        fileModifiedAt=now,
        updatedAt=now,
        livePhotoVideoId="live-vid-id" if is_live_photo else None,
    )


class TestGetPhotosForDateRange:
    """Tests for fetching photographs — Live Photo stills included."""

    @pytest.mark.asyncio
    async def test_returns_every_image_including_live_photo_stills(self):
        """A Live Photo still is a photograph and belongs in the photo pool.

        Dropping it here hid most of a real library from selection: 17 of 18
        favourites in a measured month are Live Photos. Whether the burst is
        worth showing as motion is a rendering question, asked later.
        """
        regular_photo = _make_image_asset("photo-1", is_live_photo=False)
        live_photo = _make_image_asset("live-1", is_live_photo=True)

        # WHY: mock search_metadata to return both regular and live photos
        request_fn = AsyncMock()
        service = SearchService(request_fn)
        service.search_metadata = AsyncMock(
            return_value=MetadataSearchResult(
                assets={
                    "items": [
                        regular_photo.model_dump(by_alias=True),
                        live_photo.model_dump(by_alias=True),
                    ]
                },
                nextPage=None,
            )
        )

        date_range = DateRange(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 12, 31, tzinfo=UTC),
        )
        result = await service.get_photos_for_date_range(date_range)

        assert {a.id for a in result} == {"photo-1", "live-1"}

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_images(self):
        """Returns empty list when no images found."""
        request_fn = AsyncMock()
        service = SearchService(request_fn)
        service.search_metadata = AsyncMock(
            return_value=MetadataSearchResult(
                assets={"items": []},
                nextPage=None,
            )
        )

        date_range = DateRange(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 12, 31, tzinfo=UTC),
        )
        result = await service.get_photos_for_date_range(date_range)
        assert result == []

    @pytest.mark.asyncio
    async def test_queries_with_image_asset_type(self):
        """Search uses AssetType.IMAGE to fetch photos."""
        request_fn = AsyncMock()
        service = SearchService(request_fn)
        service.search_metadata = AsyncMock(
            return_value=MetadataSearchResult(
                assets={"items": []},
                nextPage=None,
            )
        )

        date_range = DateRange(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 12, 31, tzinfo=UTC),
        )
        await service.get_photos_for_date_range(date_range)

        call_kwargs = service.search_metadata.call_args.kwargs
        assert call_kwargs["asset_type"] == AssetType.IMAGE

    @pytest.mark.asyncio
    async def test_person_filter(self):
        """get_photos_for_date_range with person_id filters by person."""
        photo = _make_image_asset("photo-1")

        request_fn = AsyncMock()
        service = SearchService(request_fn)
        service.search_metadata = AsyncMock(
            return_value=MetadataSearchResult(
                assets={"items": [photo.model_dump(by_alias=True)]},
                nextPage=None,
            )
        )

        date_range = DateRange(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 12, 31, tzinfo=UTC),
        )
        result = await service.get_photos_for_date_range(date_range, person_id="person-abc")

        call_kwargs = service.search_metadata.call_args.kwargs
        assert call_kwargs["person_ids"] == ["person-abc"]
        assert len(result) == 1
