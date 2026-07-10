"""Upload and album management API service."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from immich_memories.api.models import Asset

RequestFn = Callable[..., Any]

logger = logging.getLogger(__name__)


class AlbumService:
    """Upload and album management operations against the Immich API."""

    def __init__(self, request_fn: RequestFn) -> None:
        self._request = request_fn

    async def upload_asset(self, file_path: Path) -> str:
        """Upload a file to Immich. Returns the asset ID."""
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        stat = file_path.stat()
        file_hash = hashlib.sha256(file_path.name.encode() + str(stat.st_size).encode())
        device_asset_id = f"immich-memories-{file_hash.hexdigest()[:16]}"
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()

        with file_path.open("rb") as f:
            data = await self._request(
                "POST",
                "/assets",
                data={
                    "deviceAssetId": device_asset_id,
                    "deviceId": "immich-memories",
                    "fileCreatedAt": mtime,
                    "fileModifiedAt": mtime,
                },
                files={"assetData": (file_path.name, f, "video/mp4")},
                timeout=600.0,  # 10 min for large video uploads on slow connections
            )
        return data["id"]

    async def create_album(self, name: str, description: str | None = None) -> str:
        """Create an album in Immich. Returns the album ID."""
        body: dict = {"albumName": name}
        if description:
            body["description"] = description
        data = await self._request("POST", "/albums", json=body)
        return data["id"]

    async def add_assets_to_album(self, album_id: str, asset_ids: list[str]) -> None:
        await self._request("PUT", f"/albums/{album_id}/assets", json={"ids": asset_ids})

    async def get_albums(self) -> list[dict]:
        return await self._request("GET", "/albums")

    async def get_album(self, album_id: str) -> dict:
        """Fetch a single album by ID, including its assets."""
        return await self._request("GET", f"/albums/{album_id}")

    async def get_album_assets(self, album_id: str) -> list[Asset]:
        """Fetch all assets in an album as parsed Asset models.

        Trashed assets are skipped; archived assets are kept because their
        presence in an album is a deliberate user choice. Assets that fail
        to parse are logged and skipped rather than failing the whole fetch.
        Results are sorted chronologically by capture time.
        """
        data = await self.get_album(album_id)
        assets: list[Asset] = []
        for raw in data.get("assets", []):
            try:
                asset = Asset.model_validate(raw)
            except ValidationError as exc:
                logger.warning("Skipping unparseable album asset %s: %s", raw.get("id"), exc)
                continue
            if asset.is_trashed:
                continue
            assets.append(asset)
        assets.sort(key=lambda a: a.file_created_at)
        return assets

    async def find_album_by_name(self, name: str) -> str | None:
        """Returns album ID if found, None otherwise."""
        albums = await self.get_albums()
        for album in albums:
            if album.get("albumName") == name:
                return album["id"]
        return None

    async def upload_memory(
        self, video_path: Path, album_name: str | None = None
    ) -> dict[str, str | None]:
        """Upload a generated memory video, optionally adding it to an album.

        Reuses existing album if one with the same name exists.
        """
        asset_id = await self.upload_asset(video_path)

        album_id = None
        if album_name:
            album_id = await self.find_album_by_name(album_name)
            if album_id is None:
                album_id = await self.create_album(album_name)
            await self.add_assets_to_album(album_id, [asset_id])

        return {"asset_id": asset_id, "album_id": album_id}
