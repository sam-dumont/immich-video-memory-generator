"""Which album a cut mostly sits in, for the title to borrow its name from.

A family day is often named by nothing but the album somebody filed it under.
Reading that name back is allowed where inventing one is not, so the answer has
to be a majority and not a coincidence.
"""

from __future__ import annotations

import pytest

from immich_memories.api.album_service import AlbumService


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

    assert await service.album_holding_most(["a1", "a2", "a3", "a4"]) == "Sunday at the lake"


@pytest.mark.asyncio
async def test_an_album_holding_less_than_half_named_nothing() -> None:
    """Two pictures out of five in one album is a coincidence, not an occasion."""
    service = _service({"a1": [_BEST], "a2": [_BEST]})

    assert await service.album_holding_most(["a1", "a2", "a3", "a4", "a5"]) is None


@pytest.mark.asyncio
async def test_assets_in_no_album_at_all_answer_nothing() -> None:
    assert await _service({}).album_holding_most(["a1", "a2"]) is None
    assert await _service({}).album_holding_most([]) is None


@pytest.mark.asyncio
async def test_the_sample_is_capped_so_a_long_film_costs_a_bounded_number_of_reads() -> None:
    """One request per asset: a 300-clip film must not become 300 requests."""
    asked: list[str] = []

    async def request(method: str, endpoint: str, **kwargs):
        asked.append(kwargs["params"]["assetId"])
        return [_SUNDAY]

    async def version():  # pragma: no cover
        raise AssertionError

    service = AlbumService(request, version)
    name = await service.album_holding_most([f"a{n}" for n in range(300)], limit=40)

    assert name == "Sunday at the lake"
    assert len(asked) == 40


@pytest.mark.asyncio
async def test_one_unreadable_asset_does_not_cost_the_whole_answer() -> None:
    async def request(method: str, endpoint: str, **kwargs):
        if kwargs["params"]["assetId"] == "gone":
            raise RuntimeError("404")
        return [_SUNDAY]

    async def version():  # pragma: no cover
        raise AssertionError

    service = AlbumService(request, version)

    assert await service.album_holding_most(["a1", "gone", "a3"]) == "Sunday at the lake"
