"""A playback byte range comes back with the rendition's full size, or says why it cannot."""

import httpx
import pytest

from immich_memories.api.asset_service import AssetService

PLAYBACK = bytes(range(256)) * 4


def serve(request: httpx.Request) -> httpx.Response:
    # WHY: the Immich playback endpoint; a MockTransport answers ranges the way it does
    if request.url.path.endswith("/missing/video/playback"):
        return httpx.Response(404, json={"message": "Not found"})
    start, end = request.headers["range"].removeprefix("bytes=").split("-")
    body = PLAYBACK[int(start) : int(end) + 1]
    last = int(start) + len(body) - 1
    return httpx.Response(
        206, headers={"content-range": f"bytes {start}-{last}/{len(PLAYBACK)}"}, content=body
    )


async def read(asset_id, start, length, handler=serve):
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="https://immich.test", transport=transport) as client:
        service = AssetService(None, "https://immich.test", lambda: client)
        return await service.get_video_playback_range(asset_id, start, length)


@pytest.mark.asyncio
async def test_a_range_answers_its_bytes_and_the_full_size():
    assert await read("clip", 16, 8) == (PLAYBACK[16:24], len(PLAYBACK))


@pytest.mark.asyncio
async def test_a_server_that_ignores_ranges_still_answers_the_asked_bytes():
    whole = await read(
        "clip", 16, 8, handler=lambda _request: httpx.Response(200, content=PLAYBACK)
    )
    assert whole == (PLAYBACK[16:24], len(PLAYBACK))


@pytest.mark.asyncio
async def test_a_missing_playback_is_a_404_the_caller_can_read():
    with pytest.raises(httpx.HTTPStatusError) as refused:
        await read("missing", 0, 8)
    assert refused.value.response.status_code == 404
