"""Every person comes back, however many pages Immich splits /people into."""

import httpx
import pytest

from immich_memories.api.immich import ImmichClient


def people_of(total: int):
    """A /people that pages the way Immich v2 and v3 do: `size` rows, then `hasNextPage`."""
    pages: list[int] = []

    def serve(request: httpx.Request) -> httpx.Response:
        # WHY: Immich's people endpoint; a MockTransport pages it like the server
        page = int(request.url.params.get("page", 1))
        size = int(request.url.params.get("size", 500))
        pages.append(page)
        rows = range((page - 1) * size, min(page * size, total))
        return httpx.Response(
            200,
            json={
                "people": [{"id": f"person-{i}", "name": f"P{i}"} for i in rows],
                "total": total,
                "hidden": 0,
                "hasNextPage": page * size < total,
            },
        )

    return serve, pages


async def all_people(total: int, version: str):
    serve, pages = people_of(total)
    client = ImmichClient("https://immich.test", "key", api_version=version)
    client._client = httpx.AsyncClient(
        base_url="https://immich.test", transport=httpx.MockTransport(serve)
    )
    async with client:
        return await client.get_all_people(with_hidden=True), pages


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["v2", "v3"])
async def test_a_library_of_thousands_of_people_comes_back_whole(version):
    people, pages = await all_people(3_122, version)

    assert [p.id for p in people] == [f"person-{i}" for i in range(3_122)]
    assert pages == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_a_small_library_asks_once():
    people, pages = await all_people(12, "v3")

    assert len(people) == 12
    assert pages == [1]
