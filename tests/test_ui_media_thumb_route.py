"""One media route serves every thumbnail the UI shows (#824, S3).

Before this route every picture in the app was a base64 data URI of Immich's
preview: roughly half a megabyte of text in a DOM attribute and six megabytes of
decoded bitmap per grid cell. The route serves bytes from the session's
thumbnail cache instead, so the browser caches, decodes lazily and evicts.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from immich_memories.cache.thumbnail_cache import ThumbnailCache
from immich_memories.ui.auth import is_bypass_path
from immich_memories.ui.media_route import (
    GRID_THUMBNAIL_PX,
    register_media_route,
    thumbnail_url,
)


def _jpeg(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 40)).save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def cache(tmp_path: Path) -> ThumbnailCache:
    store = ThumbnailCache(tmp_path / "thumbs")
    store.put("asset-1", "preview", _jpeg(1600, 900))
    return store


@pytest.fixture
def client(cache: ThumbnailCache) -> TestClient:
    app = FastAPI()
    register_media_route(app, lambda: cache)
    return TestClient(app)


def test_a_cached_preview_is_served_as_a_small_thumbnail(client: TestClient, cache: ThumbnailCache):
    response = client.get(thumbnail_url("asset-1"))

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert "private" in response.headers["cache-control"]
    with Image.open(io.BytesIO(response.content)) as image:
        assert max(image.size) <= GRID_THUMBNAIL_PX
    assert cache.has("asset-1", "thumbnail"), "the derived thumbnail is cached for the next request"


def test_the_preview_size_is_served_verbatim(client: TestClient, cache: ThumbnailCache):
    response = client.get(thumbnail_url("asset-1", size="preview"))

    assert response.status_code == 200
    assert response.content == cache.get("asset-1", "preview")


def test_an_unknown_asset_is_a_404(client: TestClient):
    assert client.get(thumbnail_url("asset-2")).status_code == 404


def test_a_malformed_id_or_size_never_reaches_the_cache(client: TestClient):
    assert client.get("/media/thumb/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get("/media/thumb/asset-1?size=original").status_code == 404


def test_the_route_is_not_an_auth_bypass_path():
    """The auth middleware gates every path it does not list; this one must stay unlisted."""
    assert not is_bypass_path(thumbnail_url("asset-1"))
