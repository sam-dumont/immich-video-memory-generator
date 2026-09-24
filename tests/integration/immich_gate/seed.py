"""Seed an empty Immich with the gate library, then write the config the gate runs with.

    uv run python -m tests.integration.immich_gate.seed --url http://127.0.0.1:2299 \
        --media .immich-gate/media --home .immich-gate/home

Talks to Immich over plain HTTP and never through the product's client: a
product bug must fail a gate test, not the seeding. Exits non-zero, with the
reason, when Immich does not answer within ``--timeout`` seconds -- the gate
fails, it never skips.

What it leaves behind, all of it public or synthetic:

* an admin, and an API key with every permission
* the June 2024 fixture month (133 pictures, 13 of them videos) with places, favourites
  and three people tagged by hand (no machine learning runs)
* one album per shipped story, and ``BULK_ALBUM`` holding the paging set
* ``<home>/.immich-memories/config.yaml``: rules reader, metadata-only
  preparation, no hardware, no music, nothing outside this Immich
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from tests.e2e.fake_library import CAST, STORIES, carriers_of
from tests.integration.immich_gate import media

ADMIN_EMAIL = "gate@example.invalid"
ADMIN_PASSWORD = "immich-gate-password"  # noqa: S105 -- throwaway CI stack
DEVICE_ID = "immich-gate-seed"
HOME_LATITUDE, HOME_LONGITUDE = 50.8417, 4.3624

_UPLOAD_WORKERS = 8
_QUEUE_TIMEOUT = 300.0


class SeedError(RuntimeError):
    """Seeding could not finish; the gate must fail."""


def wait_for_immich(url: str, timeout: float) -> None:
    """Return once ``/api/server/ping`` answers ``pong``, or raise after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    last = "no answer yet"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{url}/api/server/ping", timeout=5.0)
            if response.status_code == 200 and response.json().get("res") == "pong":
                return
            last = f"HTTP {response.status_code}"
        except (httpx.HTTPError, ValueError) as exc:
            last = type(exc).__name__
        time.sleep(2)
    raise SeedError(f"Immich at {url} did not answer within {timeout:.0f}s ({last})")


def _checked(response: httpx.Response) -> Any:
    if response.status_code >= 400:
        raise SeedError(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code}: {response.text[:500]}"
        )
    return response.json() if response.content else None


def _api_key(url: str) -> str:
    _checked(
        httpx.post(
            f"{url}/api/auth/admin-sign-up",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "name": "Gate"},
            timeout=30.0,
        )
    )
    login = _checked(
        httpx.post(
            f"{url}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=30.0,
        )
    )
    key = _checked(
        httpx.post(
            f"{url}/api/api-keys",
            json={"name": "immich-gate", "permissions": ["all"]},
            headers={"Authorization": f"Bearer {login['accessToken']}"},
            timeout=30.0,
        )
    )
    return key["secret"]


class Seeder:
    """Everything after the API key, over one keep-alive client."""

    def __init__(self, url: str, api_key: str) -> None:
        self.http = httpx.Client(
            base_url=f"{url}/api", headers={"x-api-key": api_key}, timeout=120.0
        )
        self.major = int(_checked(self.http.get("/server/version"))["major"])

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        return _checked(self.http.request(method, path, **kwargs))

    def upload(self, gate_file: media.GateFile) -> str:
        created = gate_file.taken_at.isoformat()
        fields = {"fileCreatedAt": created, "fileModifiedAt": created}
        picture = gate_file.picture
        if picture is not None and picture.is_favorite:
            fields["isFavorite"] = "true"
        if self.major < 3:
            fields |= {"deviceId": DEVICE_ID, "deviceAssetId": f"gate-{gate_file.path.name}"}
        content_type = "video/mp4" if gate_file.path.suffix == ".mp4" else "image/jpeg"
        with gate_file.path.open("rb") as handle:
            answer = self.call(
                "POST",
                "/assets",
                data=fields,
                files={"assetData": (gate_file.path.name, handle, content_type)},
            )
        return answer["id"]

    def upload_all(self, files: list[media.GateFile]) -> list[str]:
        with ThreadPoolExecutor(max_workers=_UPLOAD_WORKERS) as pool:
            return list(pool.map(self.upload, files))

    def wait_for_queues(self, timeout: float = _QUEUE_TIMEOUT) -> None:
        """Wait until no Immich job is active or waiting, so reads see finished assets."""
        deadline = time.monotonic() + timeout
        busy: list[str] = []
        while time.monotonic() < deadline:
            queues = self.call("GET", "/queues")
            busy = [
                f"{queue['name']}={queue['statistics']['active'] + queue['statistics']['waiting']}"
                for queue in queues
                if queue["statistics"]["active"] + queue["statistics"]["waiting"] > 0
            ]
            if not busy:
                return
            time.sleep(2)
        raise SeedError(f"Immich jobs still running after {timeout:.0f}s: {', '.join(busy)}")

    def place(self, asset_ids: Iterable[str], files: list[media.GateFile]) -> None:
        for asset_id, gate_file in zip(asset_ids, files, strict=True):
            assert gate_file.picture is not None
            where = gate_file.picture.place
            self.call(
                "PUT",
                f"/assets/{asset_id}",
                json={"latitude": where.latitude, "longitude": where.longitude},
            )

    def tag_people(self, asset_ids: list[str], files: list[media.GateFile]) -> None:
        """Create the cast and draw one face box per person per picture, by hand."""
        people = {name: self.call("POST", "/people", json={"name": name})["id"] for name in CAST}
        featured: set[str] = set()
        for asset_id, gate_file in zip(asset_ids, files, strict=True):
            assert gate_file.picture is not None
            width, height = media.VIDEO_SIZE if gate_file.picture.is_video else media.PHOTO_SIZE
            for slot, name in enumerate(gate_file.picture.people):
                self.call(
                    "POST",
                    "/faces",
                    json={
                        "assetId": asset_id,
                        "personId": people[name],
                        "imageWidth": width,
                        "imageHeight": height,
                        "x": 100 + slot * 500,
                        "y": 200,
                        "width": 300,
                        "height": 300,
                    },
                )
                if name not in featured:
                    featured.add(name)
                    self.call(
                        "PUT", f"/people/{people[name]}", json={"featureFaceAssetId": asset_id}
                    )

    def album(self, name: str, asset_ids: list[str]) -> str:
        album_id = self.call("POST", "/albums", json={"albumName": name})["id"]
        for start in range(0, len(asset_ids), 500):
            self.call(
                "PUT", f"/albums/{album_id}/assets", json={"ids": asset_ids[start : start + 500]}
            )
        return album_id


def write_config(home: Path, url: str, api_key: str) -> Path:
    """The config the gate tests and `generate` read: rules tier, nothing but this Immich."""
    state = home / "state"
    config_path = home / ".immich-memories" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    (state / "cache").mkdir(parents=True, exist_ok=True)
    (state / "output").mkdir(parents=True, exist_ok=True)
    config = {
        "immich": {"url": url, "api_key": api_key, "api_version": "auto"},
        "output": {
            "directory": str(state / "output"),
            "format": "mp4",
            "resolution": "720p",
            "codec": "h264",
            "hdr_mode": "sdr",
            "quality": "low",
        },
        "cache": {"directory": str(state / "cache"), "database": str(state / "cache" / "gate.db")},
        "upload": {"enabled": False},
        "photos": {"enabled": True},
        "trips": {"homebase_latitude": HOME_LATITUDE, "homebase_longitude": HOME_LONGITUDE},
        "advanced": {
            "hardware": {"enabled": False, "backend": "none", "gpu_decode": False},
            "musicgen": {"enabled": False},
            "ace_step": {"enabled": False},
            "editorial": {"reader": "rules", "preparation": {"tier": "metadata_only"}},
        },
    }
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    config_path.chmod(0o600)
    return config_path


def seed(url: str, media_root: Path, home: Path, timeout: float) -> None:
    started = time.monotonic()
    wait_for_immich(url, timeout)
    media.build(media_root)
    api_key = _api_key(url)
    seeder = Seeder(url, api_key)
    library = media.library_files(media_root)
    bulk = media.bulk_files(media_root)
    library_ids = seeder.upload_all(library)
    bulk_ids = seeder.upload_all(bulk)
    seeder.wait_for_queues()
    seeder.place(library_ids, library)
    seeder.tag_people(library_ids, library)
    id_of = {
        gate_file.picture.asset_id: asset_id
        for asset_id, gate_file in zip(library_ids, library, strict=True)
        if gate_file.picture
    }
    for story in STORIES:
        members = [id_of[picture.asset_id] for picture in carriers_of(story.key)]
        if members:
            seeder.album(story.title, members)
    seeder.album(media.BULK_ALBUM, bulk_ids)
    seeder.wait_for_queues()
    write_config(home, url, api_key)
    print(
        f"Seeded Immich v{seeder.major} at {url}: {len(library_ids)} fixture assets, "
        f"{len(bulk_ids)} paging assets, {len(CAST)} people "
        f"in {time.monotonic() - started:.0f}s ({datetime.now(UTC):%H:%M:%S} UTC)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    try:
        seed(args.url.rstrip("/"), args.media.resolve(), args.home.resolve(), args.timeout)
    except SeedError as exc:
        print(f"immich-gate seed FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
