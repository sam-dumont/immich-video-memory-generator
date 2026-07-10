"""Tests for the album memory type: API fetching, CLI helpers, titles."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from immich_memories.api.album_service import AlbumService
from immich_memories.cli._album_generation import (
    album_date_range,
    album_slug,
    default_album_duration,
    format_date_span,
    resolve_album,
)
from immich_memories.ui.pages.pipeline_title import generate_template_title


def _raw_asset(asset_id: str, created: str, asset_type: str = "VIDEO", **extra) -> dict:
    return {
        "id": asset_id,
        "type": asset_type,
        "fileCreatedAt": created,
        "fileModifiedAt": created,
        "updatedAt": created,
        **extra,
    }


class TestGetAlbumAssets:
    """AlbumService.get_album_assets parses, filters, and sorts album assets."""

    @pytest.mark.asyncio
    async def test_parses_and_sorts_chronologically(self):
        request = AsyncMock(
            return_value={
                "id": "album-1",
                "albumName": "Trip",
                "assets": [
                    _raw_asset("a2", "2026-07-06T10:00:00Z"),
                    _raw_asset("a1", "2026-07-04T10:00:00Z", asset_type="IMAGE"),
                ],
            }
        )
        service = AlbumService(request)

        assets = await service.get_album_assets("album-1")

        assert [a.id for a in assets] == ["a1", "a2"]
        request.assert_awaited_once_with("GET", "/albums/album-1")

    @pytest.mark.asyncio
    async def test_skips_trashed_assets(self):
        request = AsyncMock(
            return_value={
                "id": "album-1",
                "assets": [
                    _raw_asset("keep", "2026-07-04T10:00:00Z"),
                    _raw_asset("gone", "2026-07-05T10:00:00Z", isTrashed=True),
                ],
            }
        )
        service = AlbumService(request)

        assets = await service.get_album_assets("album-1")

        assert [a.id for a in assets] == ["keep"]

    @pytest.mark.asyncio
    async def test_skips_unparseable_assets(self):
        request = AsyncMock(
            return_value={
                "id": "album-1",
                "assets": [
                    {"id": "broken"},  # missing required fields
                    _raw_asset("ok", "2026-07-04T10:00:00Z"),
                ],
            }
        )
        service = AlbumService(request)

        assets = await service.get_album_assets("album-1")

        assert [a.id for a in assets] == ["ok"]

    @pytest.mark.asyncio
    async def test_keeps_archived_assets(self):
        """Archived assets stay: their album membership is deliberate."""
        request = AsyncMock(
            return_value={
                "id": "album-1",
                "assets": [_raw_asset("arch", "2026-07-04T10:00:00Z", isArchived=True)],
            }
        )
        service = AlbumService(request)

        assets = await service.get_album_assets("album-1")

        assert [a.id for a in assets] == ["arch"]


def _album(album_id: str, name: str, count: int = 5) -> dict:
    return {"id": album_id, "albumName": name, "assetCount": count}


class TestResolveAlbum:
    """resolve_album matches by ID, exact name, or unique partial."""

    def _client(self, albums: list[dict]) -> MagicMock:
        client = MagicMock()
        client.get_albums.return_value = albums
        return client

    def test_matches_by_id(self):
        albums = [_album("id-1", "Kinmen"), _album("id-2", "Ruth Series")]
        assert resolve_album(self._client(albums), "id-2")["albumName"] == "Ruth Series"

    def test_matches_exact_name_case_insensitive(self):
        albums = [_album("id-1", "Kinmen Missions"), _album("id-2", "Kinmen Missions 2025")]
        assert resolve_album(self._client(albums), "kinmen missions")["id"] == "id-1"

    def test_matches_unique_partial(self):
        albums = [_album("id-1", "Kinmen Missions Trip"), _album("id-2", "TODKids Camp")]
        assert resolve_album(self._client(albums), "kinmen")["id"] == "id-1"

    def test_ambiguous_partial_exits(self):
        albums = [_album("id-1", "Kinmen 2025"), _album("id-2", "Kinmen 2026")]
        with pytest.raises(SystemExit):
            resolve_album(self._client(albums), "kinmen")

    def test_not_found_exits(self):
        with pytest.raises(SystemExit):
            resolve_album(self._client([_album("id-1", "Kinmen")]), "nonexistent")


class TestAlbumHelpers:
    """Slug, date span, date range, and duration helpers."""

    def test_slug_sanitizes(self):
        assert album_slug("Kinmen Missions: July '26!") == "kinmen_missions_july_26"

    def test_slug_empty_fallback(self):
        assert album_slug("???") == "album"

    def test_span_single_day(self):
        assert format_date_span(date(2026, 7, 4), date(2026, 7, 4)) == "July 4, 2026"

    def test_span_same_month(self):
        assert format_date_span(date(2026, 7, 4), date(2026, 7, 12)) == "July 4 \u2013 12, 2026"

    def test_span_cross_year(self):
        result = format_date_span(date(2025, 12, 30), date(2026, 1, 2))
        assert result == "December 30, 2025 \u2013 January 2, 2026"

    def test_date_range_from_assets(self):
        a1 = MagicMock(file_created_at=datetime(2026, 7, 4, 8, 0))
        a2 = MagicMock(file_created_at=datetime(2026, 7, 12, 20, 0))
        dr = album_date_range([a2, a1], {})
        assert dr.start == datetime(2026, 7, 4, 8, 0)
        assert dr.end == datetime(2026, 7, 12, 20, 0)

    def test_date_range_falls_back_to_album_metadata(self):
        dr = album_date_range(
            [], {"startDate": "2026-07-04T00:00:00Z", "endDate": "2026-07-12T00:00:00Z"}
        )
        assert dr.start.date() == date(2026, 7, 4)
        assert dr.end.date() == date(2026, 7, 12)

    def test_duration_scales_and_clamps(self):
        assert default_album_duration(0) == 120.0
        assert default_album_duration(5) == 60.0  # floor
        assert default_album_duration(40) == 200.0
        assert default_album_duration(500) == 600.0  # ceiling


class TestAlbumTemplateTitle:
    """UI template title for album memories uses the album name."""

    def test_album_title_with_span(self):
        title, subtitle = generate_template_title(
            memory_type="album",
            start_date="2026-07-04",
            end_date="2026-07-12",
            album_name="Kinmen Missions",
        )
        assert title == "Kinmen Missions"
        assert subtitle == "2026-07-04 \u2013 2026-07-12"

    def test_album_title_single_day(self):
        title, subtitle = generate_template_title(
            memory_type="album",
            start_date="2026-07-04",
            end_date="2026-07-04",
            album_name="Baptism Sunday",
        )
        assert title == "Baptism Sunday"
        assert subtitle == "July 4, 2026"

    def test_album_title_fallback_name(self):
        title, _ = generate_template_title(
            memory_type="album",
            start_date="2026-07-04",
            end_date="2026-07-12",
        )
        assert title == "Album Memories"
