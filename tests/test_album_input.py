"""Fetching an Immich album as a memory's source pool (#270).

Albums are not always curated: 'Récentes'-style smart albums reach tens of
thousands of assets, so the fetch is paginated and bounded.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from immich_memories.api.album_service import (
    AlbumNotFoundError,
    AlbumService,
    AmbiguousAlbumError,
)
from immich_memories.api.models import AssetType, MetadataSearchResult
from immich_memories.api.search_service import SearchService

BASE = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)


def _asset_dto(asset_id: str, index: int = 0) -> dict:
    created = BASE + timedelta(minutes=index)
    return {
        "id": asset_id,
        "type": AssetType.VIDEO.value,
        "fileCreatedAt": created.isoformat(),
        "fileModifiedAt": created.isoformat(),
        "updatedAt": created.isoformat(),
    }


def _page(ids: list[str], *, next_page: str | None) -> MetadataSearchResult:
    return MetadataSearchResult(
        assets={"items": [_asset_dto(i) for i in ids], "nextPage": next_page}
    )


class TestGetAssetsForAlbum:
    @pytest.mark.asyncio
    async def test_pages_until_the_album_is_exhausted(self):
        service = SearchService(AsyncMock())
        # WHY: replaces the Immich /search/metadata endpoint; paging is the behaviour under test.
        service.search_metadata = AsyncMock(
            side_effect=[
                _page(["a", "b"], next_page="2"),
                _page(["c"], next_page=None),
            ]
        )

        assets = await service.get_assets_for_album("album-1", asset_type=AssetType.VIDEO)

        assert [a.id for a in assets] == ["a", "b", "c"]
        first_call = service.search_metadata.await_args_list[0].kwargs
        assert first_call["album_ids"] == ["album-1"]
        assert first_call["size"] == 1000

    @pytest.mark.asyncio
    async def test_stops_paging_once_the_limit_is_reached(self):
        """A 37k-asset smart album must not be pulled into memory in full."""
        service = SearchService(AsyncMock())
        service.search_metadata = AsyncMock(
            side_effect=[
                _page(["a", "b", "c"], next_page="2"),
                _page(["d", "e", "f"], next_page="3"),
            ]
        )

        assets = await service.get_assets_for_album("album-1", asset_type=AssetType.VIDEO, limit=4)

        assert [a.id for a in assets] == ["a", "b", "c", "d"]
        assert service.search_metadata.await_count == 2


def _albums_fn(*albums: dict) -> AsyncMock:
    """WHY: replaces the Immich GET /albums endpoint."""
    return AsyncMock(return_value=list(albums))


def _album(album_id: str, name: str, count: int = 0) -> dict:
    return {"id": album_id, "albumName": name, "assetCount": count}


class TestResolveAlbum:
    @pytest.mark.asyncio
    async def test_matches_a_name_and_reports_the_asset_count(self):
        service = AlbumService(
            _albums_fn(_album("a-1", "Holidays", 12), _album("a-2", "Trip 2025", 340)), AsyncMock()
        )

        ref = await service.resolve_album("Trip 2025")

        assert (ref.id, ref.name, ref.asset_count) == ("a-2", "Trip 2025", 340)

    @pytest.mark.asyncio
    async def test_matches_a_name_ignoring_case(self):
        service = AlbumService(_albums_fn(_album("a-2", "Trip 2025", 3)), AsyncMock())

        assert (await service.resolve_album("trip 2025")).id == "a-2"

    @pytest.mark.asyncio
    async def test_matches_an_album_id(self):
        service = AlbumService(_albums_fn(_album("a-2", "Trip 2025", 3)), AsyncMock())

        assert (await service.resolve_album("a-2")).name == "Trip 2025"

    @pytest.mark.asyncio
    async def test_a_duplicated_album_name_asks_for_an_id(self):
        """Real libraries carry six albums named 'Récentes' — a name is not an identifier."""
        service = AlbumService(
            _albums_fn(
                _album("a-1", "Récentes", 37318),
                _album("a-2", "Récentes", 15672),
                _album("a-3", "Autre", 4),
            ),
            AsyncMock(),
        )

        with pytest.raises(AmbiguousAlbumError) as exc:
            await service.resolve_album("Récentes")

        message = str(exc.value)
        assert "a-1" in message and "a-2" in message and "37318" in message
        assert "a-3" not in message

    @pytest.mark.asyncio
    async def test_unknown_album_lists_the_available_names(self):
        service = AlbumService(
            _albums_fn(_album("a-1", "Holidays"), _album("a-2", "Trip 2025")), AsyncMock()
        )

        with pytest.raises(AlbumNotFoundError) as exc:
            await service.resolve_album("Nope")

        assert "Holidays" in str(exc.value) and "Trip 2025" in str(exc.value)
