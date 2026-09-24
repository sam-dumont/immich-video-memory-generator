"""A retried upload never leaves two copies of a film in the library."""

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from immich_memories.api.immich import ImmichClient

EXISTING = "0b6f5c1e-7d2a-4a8e-9c3b-2f1e0d9c8b7a"


class FakeImmich:
    """Immich's upload and bulk-upload-check, keyed by the file's SHA-1 like the server."""

    def __init__(self, *, lost_answers: int, stores_before_failing: bool) -> None:
        self.lost_answers = lost_answers
        self.stores_before_failing = stores_before_failing
        self.stored: dict[str, str] = {}
        self.posts: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        # WHY: the Immich server; a MockTransport answers upload and checksum lookups
        if request.url.path == "/api/assets/bulk-upload-check":
            (item,) = json.loads(request.content)["assets"]
            if asset_id := self.stored.get(item["checksum"]):
                result = {
                    "id": item["id"],
                    "action": "reject",
                    "reason": "duplicate",
                    "assetId": asset_id,
                    "isTrashed": False,
                }
            else:
                result = {"id": item["id"], "action": "accept"}
            return httpx.Response(200, json={"results": [result]})
        assert request.url.path == "/api/assets"
        self.posts.append(request)
        checksum = request.headers["x-immich-checksum"]
        if self.lost_answers:
            self.lost_answers -= 1
            if self.stores_before_failing:
                self.stored[checksum] = EXISTING
            return httpx.Response(502, json={"message": "Bad Gateway"})
        asset_id = self.stored.setdefault(checksum, f"new-{len(self.posts)}")
        return httpx.Response(201, json={"id": asset_id, "status": "created"})


async def upload(server: FakeImmich, film: Path) -> str:
    client = ImmichClient("https://immich.test", "key", api_version="v3")
    client._client = httpx.AsyncClient(
        base_url="https://immich.test", transport=httpx.MockTransport(server)
    )
    # WHY: the retry backoff is wall-clock sleep; the test only needs its order
    with patch("immich_memories.api.immich.asyncio.sleep", AsyncMock()):
        async with client:
            return await client.upload_asset(film)


@pytest.fixture()
def film(tmp_path: Path) -> Path:
    path = tmp_path / "memory.mp4"
    path.write_bytes(b"a finished film" * 1000)
    return path


@pytest.mark.asyncio
async def test_an_upload_stored_before_its_answer_was_lost_is_not_sent_again(film):
    server = FakeImmich(lost_answers=1, stores_before_failing=True)

    asset_id = await upload(server, film)

    assert asset_id == EXISTING
    assert len(server.posts) == 1
    assert set(server.stored.values()) == {EXISTING}


@pytest.mark.asyncio
async def test_an_upload_the_server_never_stored_is_sent_again(film):
    server = FakeImmich(lost_answers=1, stores_before_failing=False)

    asset_id = await upload(server, film)

    assert asset_id == "new-2"
    assert len(server.posts) == 2


@pytest.mark.asyncio
async def test_every_upload_names_its_checksum_so_immich_can_refuse_a_copy(film):
    server = FakeImmich(lost_answers=0, stores_before_failing=False)

    await upload(server, film)

    (post,) = server.posts
    assert (
        post.headers["x-immich-checksum"]
        == hashlib.sha1(film.read_bytes(), usedforsecurity=False).hexdigest()
    )
