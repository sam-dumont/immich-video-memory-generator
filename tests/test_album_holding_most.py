"""Which album a cut mostly sits in, for the title to borrow its name from.

A family day is often named by nothing but the album somebody filed it under.
Reading that name back is allowed where inventing one is not, so the answer has
to be a majority and not a coincidence.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from immich_memories.api.album_service import AlbumService, FilmScope


def _service(by_asset: dict[str, list[dict]]) -> AlbumService:
    """WHY: replaces the Immich `GET /albums?assetId=` read, the only boundary."""

    async def request(method: str, endpoint: str, **kwargs):
        assert (method, endpoint) == ("GET", "/albums")
        return by_asset.get(kwargs["params"]["assetId"], [])

    async def version():  # pragma: no cover - never reached by these calls
        raise AssertionError("the album lookup must not need an API version")

    return AlbumService(request, version)


_SUNDAY = {"id": "album-sunday", "albumName": "Sunday at the lake", "assetCount": 40}
_BEST = {"id": "album-best", "albumName": "Best of", "assetCount": 400}

# A scope roomy enough that neither album above is out of proportion to it.
_SCOPE = FilmScope(
    start=datetime(2024, 1, 1, tzinfo=UTC), end=datetime(2024, 12, 31, tzinfo=UTC), pool=3500
)


@pytest.mark.asyncio
async def test_the_album_holding_most_of_the_cut_names_it() -> None:
    service = _service(
        {
            "a1": [_SUNDAY],
            "a2": [_SUNDAY, _BEST],
            "a3": [_SUNDAY],
            "a4": [_BEST],
        }
    )

    assert (
        await service.album_holding_most(["a1", "a2", "a3", "a4"], scope=_SCOPE)
        == "Sunday at the lake"
    )


@pytest.mark.asyncio
async def test_an_album_holding_a_minority_of_the_cut_names_nothing() -> None:
    """Two pictures out of five in one album is a coincidence, not an occasion."""
    service = _service({"a1": [_BEST], "a2": [_BEST]})

    assert await service.album_holding_most(["a1", "a2", "a3", "a4", "a5"], scope=_SCOPE) is None


@pytest.mark.asyncio
async def test_a_cut_split_over_albums_still_learns_the_one_holding_most_of_it() -> None:
    """Four of ten in the day's album, three in another, three filed nowhere.

    No album holds half, and the day is still called what the biggest of them
    calls it.
    """
    service = _service(
        {
            "a1": [_SUNDAY],
            "a2": [_SUNDAY],
            "a3": [_SUNDAY],
            "a4": [_SUNDAY],
            "a5": [_BEST],
            "a6": [_BEST],
            "a7": [_BEST],
        }
    )

    cut = [f"a{n}" for n in range(1, 11)]
    assert await service.album_holding_most(cut, scope=_SCOPE) == "Sunday at the lake"


@pytest.mark.asyncio
async def test_two_albums_holding_the_same_pictures_answer_with_the_smaller_one() -> None:
    """A catch-all that swallows the day is a worse name than the day's own album."""
    service = _service({"a1": [_SUNDAY, _BEST], "a2": [_SUNDAY, _BEST]})

    assert await service.album_holding_most(["a1", "a2"], scope=_SCOPE) == "Sunday at the lake"


@pytest.mark.asyncio
async def test_assets_in_no_album_at_all_answer_nothing() -> None:
    assert await _service({}).album_holding_most(["a1", "a2"], scope=_SCOPE) is None
    assert await _service({}).album_holding_most([], scope=_SCOPE) is None


@pytest.mark.asyncio
async def test_every_picture_of_the_cut_is_asked_so_no_cap_decides_the_answer() -> None:
    """A cut is a film's worth of pictures; all of them get a say in its name."""
    asked: list[str] = []

    async def request(method: str, endpoint: str, **kwargs):
        asked.append(kwargs["params"]["assetId"])
        return [_SUNDAY]

    async def version():  # pragma: no cover
        raise AssertionError

    service = AlbumService(request, version)
    name = await service.album_holding_most([f"a{n:03d}" for n in range(300)], scope=_SCOPE)

    assert name == "Sunday at the lake"
    assert len(asked) == 300


@pytest.mark.asyncio
async def test_one_unreadable_asset_does_not_cost_the_whole_answer() -> None:
    async def request(method: str, endpoint: str, **kwargs):
        if kwargs["params"]["assetId"] == "gone":
            raise RuntimeError("404")
        return [_SUNDAY]

    async def version():  # pragma: no cover
        raise AssertionError

    service = AlbumService(request, version)

    assert (
        await service.album_holding_most(["a1", "gone", "a3"], scope=_SCOPE) == "Sunday at the lake"
    )


def _album(album_id: str, name: str, count: int, start: str, end: str) -> dict:
    return {
        "id": album_id,
        "albumName": name,
        "assetCount": count,
        "startDate": f"{start}T09:00:00.000Z",
        "endDate": f"{end}T18:00:00.000Z",
    }


# The phone's catch-all: every picture ever taken, a decade of them.
_EVERYTHING = _album("album-everything", "Everything", 38_000, "2014-02-01", "2026-09-20")
_BEACH = _album("album-beach", "Beach trip", 180, "2024-07-06", "2024-07-19")
_YEAR_2024 = FilmScope(
    start=datetime(2024, 1, 1, tzinfo=UTC),
    end=datetime(2024, 12, 31, 23, 59, tzinfo=UTC),
    pool=3500,
)


def _cut(size: int) -> list[str]:
    return [f"p{n:03d}" for n in range(size)]


@pytest.mark.asyncio
async def test_a_library_scale_catch_all_holding_most_of_a_year_does_not_name_it() -> None:
    """121 of 127 pictures sit in the phone's catch-all, which is not an occasion."""
    cut = _cut(127)
    service = _service({asset: [_EVERYTHING] for asset in cut[:121]})

    assert await service.album_holding_most(cut, scope=_YEAR_2024) is None


@pytest.mark.asyncio
async def test_a_trip_album_holding_most_of_the_cut_still_names_it() -> None:
    cut = _cut(40)
    service = _service({asset: [_BEACH, _EVERYTHING] for asset in cut[:30]})
    trip = FilmScope(
        start=datetime(2024, 7, 5, tzinfo=UTC), end=datetime(2024, 7, 20, tzinfo=UTC), pool=210
    )

    assert await service.album_holding_most(cut, scope=trip) == "Beach trip"


@pytest.mark.asyncio
async def test_a_catch_all_spanning_the_whole_of_a_person_film_does_not_name_it() -> None:
    """Twenty years since a birth date hold the catch-all's whole span.

    Only its size gives it away: it holds twenty times the pictures the person
    is in, so most of it cannot be this film.
    """
    cut = _cut(90)
    service = _service({asset: [_EVERYTHING] for asset in cut[:80]})
    since_birth = FilmScope(
        start=datetime(2005, 12, 3, tzinfo=UTC), end=datetime(2026, 9, 23, tzinfo=UTC), pool=1800
    )

    assert await service.album_holding_most(cut, scope=since_birth) is None


@pytest.mark.asyncio
async def test_an_album_spanning_years_does_not_name_one_of_them() -> None:
    """A mid-size shared dump is small next to the year, but most of its span is elsewhere."""
    shared = _album("album-shared", "Family shared", 600, "2016-05-01", "2025-03-01")
    cut = _cut(20)
    service = _service({asset: [shared] for asset in cut[:15]})

    assert await service.album_holding_most(cut, scope=_YEAR_2024) is None


@pytest.mark.asyncio
async def test_pictures_only_a_catch_all_holds_count_as_filed_nowhere() -> None:
    """The catch-all's 121 pictures must not let a 10-picture trip album win the year."""
    cut = _cut(127)
    by_asset = {asset: [_EVERYTHING] for asset in cut[:121]}
    by_asset |= {asset: [_BEACH, _EVERYTHING] for asset in cut[:10]}
    service = _service(by_asset)

    assert await service.album_holding_most(cut, scope=_YEAR_2024) is None


@pytest.mark.asyncio
async def test_an_album_made_for_the_person_still_names_their_film() -> None:
    growing_up = _album("album-growing", "Growing up", 250, "2006-01-10", "2025-12-24")
    cut = _cut(90)
    service = _service({asset: [growing_up, _EVERYTHING] for asset in cut[:60]})
    since_birth = FilmScope(
        start=datetime(2005, 12, 3, tzinfo=UTC), end=datetime(2026, 9, 23, tzinfo=UTC), pool=1800
    )

    assert await service.album_holding_most(cut, scope=since_birth) == "Growing up"
