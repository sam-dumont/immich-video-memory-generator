"""A re-render replaces its earlier upload on Immich v2 and v3 alike.

Immich v3 dropped `deviceId` and `deviceAssetId` from both the upload form and
the asset response (checked against the v2.7.5 and v3.1.0 OpenAPI specs), so the
v2 device identity cannot recognise a v3 render. The provenance tag can, on both.

Immich reads every upload in a background job, and that job ends by writing the
file's own tag list over the asset's tags. A tag applied while the read is still
running is wiped (#1270), so the fake library models that read too.
"""

import json
import re
from itertools import count

import httpx
import pytest

from immich_memories.api import album_service
from immich_memories.api.generated_asset_tags import GENERATED_MEMORY_TAG
from immich_memories.api.immich import ImmichClient

FORM_FIELD = re.compile(
    rb'Content-Disposition: form-data; name="(\w+)"(?:; filename="([^"]*)")?[^\r]*\r\n'
    rb"(?:[\w-]+: [^\r]*\r\n)*\r\n(.*?)\r\n--",
    re.S,
)


class FakeLibrary:
    """The Immich endpoints an upload touches, answering in one API version's shapes."""

    def __init__(self, version: str) -> None:
        self.version = version
        self.ids = (f"00000000-0000-4000-8000-{n:012d}" for n in count(1))
        self.assets: dict[str, dict] = {}
        self.albums: dict[str, dict] = {}
        self.tags: dict[str, str] = {}
        self.tagged: dict[str, set[str]] = {}

    # Immich's metadata job outlives the upload request: here it finishes after
    # two more requests, like a server that is still running exiftool on the
    # film while the client carries on.
    READ_TAKES_REQUESTS = 2

    def own_video(self, album: str, filename: str) -> str:
        """A video the user shot and filed in the album themselves."""
        asset_id = next(self.ids)
        self.assets[asset_id] = {"id": asset_id, "originalFileName": filename, "trashed": False}
        self.albums[album]["assets"].add(asset_id)
        return asset_id

    def trashed(self) -> set[str]:
        return {a["id"] for a in self.assets.values() if a["trashed"]}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        # WHY: the Immich server; a MockTransport answers in the version's wire shapes
        route = (request.method, re.sub(r"[0-9a-f-]{36}", "{id}", request.url.path))
        is_json = request.headers.get("content-type") == "application/json"
        body = json.loads(request.content) if is_json else None
        handlers = {
            ("POST", "/api/assets"): lambda: self._upload(request.content),
            ("GET", "/api/albums"): self._list_albums,
            ("POST", "/api/albums"): lambda: self._create_album(body),
            ("PUT", "/api/albums/{id}/assets"): lambda: self._add(request.url.path, body),
            ("POST", "/api/search/metadata"): lambda: self._search(body),
            ("GET", "/api/tags"): lambda: [{"id": i, "value": v} for i, v in self.tags.items()],
            ("PUT", "/api/tags"): lambda: self._upsert_tags(body),
            ("PUT", "/api/tags/{id}/assets"): lambda: self._tag(request.url.path, body),
            ("DELETE", "/api/assets"): lambda: self._trash(body),
            ("GET", "/api/assets/{id}"): lambda: self._asset(request.url.path),
        }
        response = httpx.Response(200, json=handlers[route]())
        if route != ("POST", "/api/assets"):
            self._advance_reads()
        return response

    def _advance_reads(self) -> None:
        for asset_id, asset in self.assets.items():
            if asset.get("reading", 0) > 0:
                asset["reading"] -= 1
                if asset["reading"] == 0:
                    # The read writes the file's own (empty) tag list over the asset.
                    for tagged in self.tagged.values():
                        tagged.discard(asset_id)

    def _asset(self, path: str) -> dict:
        asset_id = path.split("/")[3]
        read = self.assets[asset_id].get("reading", 0) == 0
        return {
            "id": asset_id,
            "exifInfo": {
                "fileSizeInByte": 12,
                "modifyDate": "2024-06-30T12:00:00.000Z" if read else None,
            },
            "tags": [
                {"id": tag_id, "value": self.tags[tag_id]}
                for tag_id, ids in self.tagged.items()
                if asset_id in ids
            ],
        }

    def _upload(self, content: bytes) -> dict:
        fields = {m[1].decode(): (m[3], m[2]) for m in FORM_FIELD.finditer(content)}
        asset_id = next(self.ids)
        asset = {"id": asset_id, "trashed": False, "reading": self.READ_TAKES_REQUESTS}
        if self.version == "v2":
            asset["deviceId"] = fields["deviceId"][0].decode()
            asset["deviceAssetId"] = fields["deviceAssetId"][0].decode()
            asset["originalFileName"] = fields["assetData"][1].decode()
        else:
            assert "deviceId" not in fields
            asset["originalFileName"] = fields["filename"][0].decode()
        self.assets[asset_id] = asset
        return {"id": asset_id, "status": "created"}

    def _list_albums(self) -> list[dict]:
        return [
            {"id": a["id"], "albumName": a["albumName"], "assetCount": len(a["assets"])}
            for a in self.albums.values()
        ]

    def _create_album(self, body: dict) -> dict:
        album_id = next(self.ids)
        self.albums[album_id] = {"id": album_id, "albumName": body["albumName"], "assets": set()}
        return {"id": album_id}

    def _add(self, path: str, body: dict) -> list:
        self.albums[path.split("/")[3]]["assets"].update(body["ids"])
        return []

    def _search(self, body: dict) -> dict:
        rows = [a for a in self.assets.values() if not a["trashed"]]
        for album_id in body.get("albumIds", ()):
            rows = [a for a in rows if a["id"] in self.albums[album_id]["assets"]]
        for tag_id in body.get("tagIds", ()):
            rows = [a for a in rows if a["id"] in self.tagged.get(tag_id, set())]
        shown = ("id", "originalFileName") + (
            ("deviceId", "deviceAssetId") if self.version == "v2" else ()
        )
        items = [{k: a[k] for k in shown if k in a} for a in rows]
        return {"assets": {"items": items, "nextPage": None}}

    def _upsert_tags(self, body: dict) -> list[dict]:
        found = []
        for value in body["tags"]:
            tag_id = next((i for i, v in self.tags.items() if v == value), None) or next(self.ids)
            self.tags[tag_id] = value
            found.append({"id": tag_id, "value": value, "name": value.rpartition("/")[2]})
        return found

    def _tag(self, path: str, body: dict) -> list[dict]:
        self.tagged.setdefault(path.split("/")[3], set()).update(body["ids"])
        return [{"id": i, "success": True} for i in body["ids"]]

    def _trash(self, body: dict) -> dict:
        for asset_id in body["ids"]:
            self.assets[asset_id]["trashed"] = True
        return {}


async def deliver(library: FakeLibrary, film, album: str = "Memories") -> str:
    client = ImmichClient("https://immich.test", "key", api_version=library.version)
    client._client = httpx.AsyncClient(
        base_url="https://immich.test", transport=httpx.MockTransport(library)
    )
    async with client:
        return (await client.upload_memory(film, album))["asset_id"]


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    # WHY: the client polls a real server on a wall clock; the fake answers at once
    monkeypatch.setattr(album_service, "_POLL_SECONDS", 0.0)


@pytest.fixture()
def film(tmp_path):
    path = tmp_path / "june_a1b2c3d4.mp4"
    path.write_bytes(b"first render")
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["v2", "v3"])
async def test_a_re_render_trashes_the_earlier_upload(version, film):
    library = FakeLibrary(version)

    first = await deliver(library, film)
    film.write_bytes(b"second render, re-encoded")
    second = await deliver(library, film)

    assert library.trashed() == {first}
    assert second not in library.trashed()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["v2", "v3"])
async def test_a_video_the_user_filed_under_the_same_name_is_kept(version, film):
    library = FakeLibrary(version)
    await deliver(library, film)
    (album_id,) = library.albums
    theirs = library.own_video(album_id, film.name)

    film.write_bytes(b"second render, re-encoded")
    await deliver(library, film)

    assert theirs not in library.trashed()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["v2", "v3"])
async def test_every_delivered_film_carries_the_provenance_tag(version, film):
    library = FakeLibrary(version)

    asset_id = await deliver(library, film)

    (tag_id,) = [i for i, v in library.tags.items() if v == GENERATED_MEMORY_TAG]
    assert library.tagged[tag_id] == {asset_id}
