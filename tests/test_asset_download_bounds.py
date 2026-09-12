"""Original downloads remain bounded when metadata admits a large recording."""

from types import SimpleNamespace

import httpx
import pytest

from immich_memories.api.asset_service import (
    DEFAULT_DOWNLOAD_LIMIT,
    AssetService,
    large_original_size,
)
from immich_memories.api.immich import SyncImmichClient
from immich_memories.cache.video_cache import VideoDownloadCache
from immich_memories.generate_downloads import _download_temporary_asset


class RecordingStream(httpx.AsyncByteStream):
    def __init__(self, payload, failure=None):
        self.payload = payload
        self.failure = failure
        self.started = False

    async def __aiter__(self):
        self.started = True
        yield self.payload
        if self.failure:
            raise self.failure


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["9", "3"])
async def test_header_rejection_happens_before_reading_body(tmp_path, header):
    stream = RecordingStream(b"abcdefgh")
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, headers={"content-length": header}, stream=stream)
    )
    async with httpx.AsyncClient(base_url="https://immich.test", transport=transport) as client:
        service = AssetService(None, "https://immich.test", lambda: client)
        with pytest.raises(ValueError):
            await service.download_asset(
                "recording", tmp_path / "original.part", max_size_bytes=4, expected_size_bytes=8
            )
    assert not stream.started
    assert not (tmp_path / "original.part").exists()


@pytest.mark.asyncio
async def test_unknown_size_header_cannot_bypass_default_bound(tmp_path):
    stream = RecordingStream(b"abcdefgh")
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, headers={"content-length": "8"}, stream=stream)
    )
    async with httpx.AsyncClient(base_url="https://immich.test", transport=transport) as client:
        service = AssetService(None, "https://immich.test", lambda: client)
        with pytest.raises(ValueError, match="exceeds limit"):
            await service.download_asset("recording", tmp_path / "original.part", max_size_bytes=4)
    assert not stream.started


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, "invalid", "8"])
async def test_exact_known_size_admits_original_above_fallback(tmp_path, header):
    stream = RecordingStream(b"abcdefgh")
    headers = {} if header is None else {"content-length": header}
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, headers=headers, stream=stream)
    )
    async with httpx.AsyncClient(base_url="https://immich.test", transport=transport) as client:
        service = AssetService(None, "https://immich.test", lambda: client)
        path = await service.download_asset(
            "recording", tmp_path / "original.part", max_size_bytes=4, expected_size_bytes=8
        )
    assert path.read_bytes() == b"abcdefgh"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected", "failure"),
    [
        (b"abcdefghi", 8, None),
        (b"abc", 8, None),
        (b"abcde", None, None),
        (b"abc", 8, httpx.ReadError("interrupted")),
    ],
)
async def test_failed_stream_never_leaves_partial_original(tmp_path, payload, expected, failure):
    stream = RecordingStream(payload, failure)
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, stream=stream))
    async with httpx.AsyncClient(base_url="https://immich.test", transport=transport) as client:
        service = AssetService(None, "https://immich.test", lambda: client)
        with pytest.raises((ValueError, httpx.ReadError)):
            await service.download_asset(
                "recording",
                tmp_path / "original.part",
                max_size_bytes=4,
                expected_size_bytes=expected,
            )
    assert not (tmp_path / "original.part").exists()


def test_exact_size_reaches_transport_through_both_client_wrappers(tmp_path):
    client = SyncImmichClient("https://immich.test", "test-key")
    client._async_client._client = httpx.AsyncClient(
        base_url="https://immich.test",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"abcdefgh")),
    )
    try:
        path = client.download_asset("recording", tmp_path / "original", expected_size_bytes=8)
        assert path.read_bytes() == b"abcdefgh"
        with pytest.raises(ValueError):
            client.download_asset("recording", tmp_path / "wrong", expected_size_bytes=7)
    finally:
        client.close()


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("companion", [None, "motion-component"])
def test_production_download_paths_use_only_the_originals_own_size(tmp_path, cached, companion):
    expected = DEFAULT_DOWNLOAD_LIMIT + 7
    asset = SimpleNamespace(
        id="recording",
        type="VIDEO",
        original_file_name="recording.mp4",
        live_photo_video_id=companion,
        exif_info=SimpleNamespace(file_size_in_byte=expected),
    )
    calls = []

    def download(asset_id, path, **kwargs):
        calls.append((asset_id, kwargs))
        path.write_bytes(b"complete")
        return path

    client = SimpleNamespace(download_asset=download)
    if cached:
        cache = VideoDownloadCache(cache_dir=tmp_path / "cache", max_size_gb=1)
        with cache.begin_batch() as batch:
            path = batch.download_or_get(client, asset)
            assert path.read_bytes() == b"complete"
            assert not list(path.parent.glob("*.part"))
    else:
        path = _download_temporary_asset(client, asset, tmp_path)
        assert path.read_bytes() == b"complete"
    assert calls == [
        (companion or asset.id, {} if companion else {"expected_size_bytes": expected})
    ]


@pytest.mark.parametrize("size", [None, -1, 0, True, "large", DEFAULT_DOWNLOAD_LIMIT])
def test_unknown_or_ordinary_size_keeps_default_limit(size):
    asset = SimpleNamespace(
        live_photo_video_id=None, exif_info=SimpleNamespace(file_size_in_byte=size)
    )
    assert large_original_size(asset) is None
