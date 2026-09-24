"""An album's assets come back whole, however many pages Immich splits them into."""

import json

import httpx
import pytest

from immich_memories.api.immich import ImmichClient

ALBUM = "4f1c9a7e-2b3d-4c5e-8f90-1a2b3c4d5e6f"


def album_of(total: int):
    """A /search/metadata that pages the way Immich does: `size` items, then `nextPage`."""
    requests: list[dict] = []

    def serve(request: httpx.Request) -> httpx.Response:
        # WHY: Immich's metadata search endpoint; a MockTransport pages it like the server
        body = json.loads(request.content)
        requests.append(body)
        assert body["albumIds"] == [ALBUM]
        page, size = int(body.get("page", 1)), int(body["size"])
        items = [{"id": f"asset-{i}"} for i in range((page - 1) * size, min(page * size, total))]
        more = page * size < total
        return httpx.Response(
            200,
            json={
                "assets": {
                    "items": items,
                    "count": len(items),
                    "total": len(items),
                    "nextPage": str(page + 1) if more else None,
                }
            },
        )

    return serve, requests


async def list_album(total: int) -> tuple[list[dict], list[dict]]:
    serve, requests = album_of(total)
    client = ImmichClient("https://immich.test", "key", api_version="v3")
    client._client = httpx.AsyncClient(
        base_url="https://immich.test", transport=httpx.MockTransport(serve)
    )
    async with client:
        return await client.list_album_assets(ALBUM), requests


@pytest.mark.asyncio
async def test_an_album_bigger_than_one_page_comes_back_whole():
    assets, requests = await list_album(2_345)

    assert [a["id"] for a in assets] == [f"asset-{i}" for i in range(2_345)]
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_an_album_of_exactly_one_page_asks_once():
    assets, requests = await list_album(1_000)

    assert len(assets) == 1_000
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_an_empty_album_is_empty():
    assets, _ = await list_album(0)

    assert assets == []
