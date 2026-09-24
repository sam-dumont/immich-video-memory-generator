"""Downloads and playback reads ride out a transient failure, and give up on a real one."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from immich_memories.api.asset_service import AssetService

ORIGINAL = b"original bytes " * 4096
PLAYBACK = b"playback rendition " * 4096


class Flaky:
    """An Immich that fails the first requests, then answers like the server."""

    def __init__(self, *failures) -> None:
        self.failures = list(failures)
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        # WHY: the Immich asset endpoints; a MockTransport fails them on cue
        self.paths.append(request.url.path)
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, int):
                return httpx.Response(failure, json={"message": "nope"})
            raise failure("dropped", request=request)
        if request.url.path.endswith("/original"):
            return httpx.Response(200, content=ORIGINAL)
        start, end = request.headers.get("range", f"bytes=0-{len(PLAYBACK) - 1}")[6:].split("-")
        body = PLAYBACK[int(start) : int(end) + 1]
        if "range" not in request.headers:
            return httpx.Response(200, content=body)
        return httpx.Response(
            206, headers={"content-range": f"bytes {start}-{end}/{len(PLAYBACK)}"}, content=body
        )


async def run(server: Flaky, call):
    transport = httpx.MockTransport(server)
    async with httpx.AsyncClient(base_url="https://immich.test", transport=transport) as client:
        service = AssetService(None, "https://immich.test", lambda: client)
        # WHY: the backoff is wall-clock sleep; the tests only need its count
        with patch("immich_memories.api.asset_service.asyncio.sleep", AsyncMock()):
            return await call(service)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [503, 429, httpx.ConnectError, httpx.ReadTimeout])
async def test_a_download_rides_out_one_transient_failure(tmp_path, failure):
    server = Flaky(failure)
    target = tmp_path / "clip.mov"

    await run(server, lambda s: s.download_asset("clip", target))

    assert target.read_bytes() == ORIGINAL
    assert len(server.paths) == 2


@pytest.mark.asyncio
async def test_a_download_that_keeps_failing_gives_up_after_three_tries(tmp_path):
    server = Flaky(502, 502, 502, 502)
    target = tmp_path / "clip.mov"

    with pytest.raises(httpx.HTTPStatusError):
        await run(server, lambda s: s.download_asset("clip", target))

    assert len(server.paths) == 3
    assert not target.exists()


@pytest.mark.asyncio
async def test_a_missing_asset_is_not_asked_again(tmp_path):
    server = Flaky(404)

    with pytest.raises(httpx.HTTPStatusError):
        await run(server, lambda s: s.download_asset("gone", tmp_path / "gone.mov"))

    assert len(server.paths) == 1


@pytest.mark.asyncio
async def test_a_playback_range_rides_out_a_transient_failure():
    server = Flaky(httpx.RemoteProtocolError)

    answer = await run(server, lambda s: s.get_video_playback_range("clip", 16, 8))

    assert answer == (PLAYBACK[16:24], len(PLAYBACK))


@pytest.mark.asyncio
async def test_the_playback_rendition_streams_to_a_file(tmp_path):
    server = Flaky(500)
    target = tmp_path / "playback.mp4"

    written = await run(server, lambda s: s.download_playback("clip", target))

    assert written == target
    assert target.read_bytes() == PLAYBACK
    assert server.paths == ["/api/assets/clip/video/playback"] * 2
