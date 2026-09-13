"""One route serves every face crop the People page shows (#824, S7).

The page used to fetch one crop per person through the API before drawing, and
inline each as a base64 data URI. The route serves the crop from the session
thumbnail cache, fetching it from Immich once when it is missing, so the
browser loads faces lazily as the roster scrolls and the API key stays on the
server.
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
    person_thumbnail_url,
    register_person_route,
)


def _jpeg(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (40, 120, 200)).save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def cache(tmp_path: Path) -> ThumbnailCache:
    return ThumbnailCache(tmp_path / "thumbs")


def _client(cache: ThumbnailCache, fetched: dict[str, bytes], calls: list[str]) -> TestClient:
    def fetch(person_id: str) -> bytes | None:
        calls.append(person_id)
        return fetched.get(person_id)

    app = FastAPI()
    register_person_route(app, lambda: cache, fetch)
    return TestClient(app)


def test_a_face_is_fetched_once_then_served_from_the_cache(cache: ThumbnailCache):
    calls: list[str] = []
    client = _client(cache, {"abc-123": _jpeg(600, 600)}, calls)

    first = client.get(person_thumbnail_url("abc-123"))
    second = client.get(person_thumbnail_url("abc-123"))

    assert first.status_code == 200 and second.status_code == 200
    assert first.headers["content-type"] == "image/jpeg"
    assert "private" in first.headers["cache-control"]
    assert calls == ["abc-123"], "the second request must come from the cache"
    with Image.open(io.BytesIO(second.content)) as image:
        assert max(image.size) <= 128, "a face crop is served at avatar size"


def test_a_person_immich_does_not_know_is_a_404(cache: ThumbnailCache):
    client = _client(cache, {}, [])
    assert client.get(person_thumbnail_url("nobody")).status_code == 404


def test_a_manual_person_never_reaches_immich(cache: ThumbnailCache):
    calls: list[str] = []
    client = _client(cache, {}, calls)

    response = client.get("/media/person/manual:abc")

    assert response.status_code == 404
    assert calls == []


def test_the_person_route_is_not_an_auth_bypass_path():
    assert not is_bypass_path(person_thumbnail_url("abc-123"))
