"""Upload and album management API service."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from immich_memories.api.compatibility import ResolvedApiVersion

RequestFn = Callable[..., Any]
ApiVersionFn = Callable[[], Awaitable[ResolvedApiVersion]]

_UPLOAD_MEDIA_TYPES = {
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
}


logger = logging.getLogger(__name__)


class InvalidUploadResponse(ValueError):
    """Raised when Immich returns a malformed successful upload response."""


def build_upload_fields(
    version: ResolvedApiVersion, file_path: Path, modified_at: datetime
) -> dict[str, str]:
    """Build deterministic request fields without opening or reading file contents.

    V2 identity intentionally retains the legacy hash of the real filename and file size.
    """
    timestamp = modified_at.isoformat()
    common_fields = {
        "fileCreatedAt": timestamp,
        "fileModifiedAt": timestamp,
    }
    if version is ResolvedApiVersion.V3:
        return {"filename": file_path.name} | common_fields

    file_hash = hashlib.sha256(file_path.name.encode() + str(file_path.stat().st_size).encode())
    identity_fields = {
        "deviceAssetId": f"immich-memories-{file_hash.hexdigest()[:16]}",
        "deviceId": "immich-memories",
    }
    return identity_fields | common_fields


def _upload_asset_id(data: Any) -> str:
    if not isinstance(data, dict):
        raise InvalidUploadResponse("Upload response must contain a non-empty string id")
    asset_id = data.get("id")
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise InvalidUploadResponse("Upload response must contain a non-empty string id")
    return asset_id


def _upload_media_type(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    try:
        return _UPLOAD_MEDIA_TYPES[suffix]
    except KeyError as exc:
        raise ValueError(f"Unsupported upload file suffix: {suffix or '<none>'}") from exc


class AlbumService:
    """Upload and album management operations against the Immich API."""

    def __init__(self, request_fn: RequestFn, api_version_fn: ApiVersionFn) -> None:
        self._request = request_fn
        self._get_api_version = api_version_fn

    async def upload_asset(self, file_path: Path) -> str:
        """Upload a file to Immich. Returns the asset ID."""
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content_type = _upload_media_type(file_path)
        version = await self._get_api_version()
        stat = file_path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        fields = build_upload_fields(version, file_path, modified_at)

        with file_path.open("rb") as f:
            data = await self._request(
                "POST",
                "/assets",
                data=fields,
                files={"assetData": (file_path.name, f, content_type)},
                timeout=600.0,  # 10 min for large video uploads on slow connections
            )
        return _upload_asset_id(data)

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

    async def find_album_by_name(self, name: str) -> str | None:
        """Returns album ID if found, None otherwise."""
        albums = await self.get_albums()
        for album in albums:
            if album.get("albumName") == name:
                return album["id"]
        return None

    async def list_album_assets(self, album_id: str) -> list[dict]:
        """Assets in an album, via search: /albums/{id} omits them on Immich 3.x."""
        data = await self._request(
            "POST", "/search/metadata", json={"albumIds": [album_id], "size": 1000}
        )
        return list(data.get("assets", {}).get("items", []))

    async def trash_assets(self, asset_ids: list[str]) -> None:
        """Move assets to Immich's trash. Recoverable; never a hard delete."""
        await self._request("DELETE", "/assets", json={"ids": asset_ids, "force": False})

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

        # WHY: the upload has already succeeded. Failing the delivery because the
        # tidy-up of a previous copy did not work would turn a working memory into
        # a reported failure, so this never propagates.
        try:
            superseded = await supersede_previous_renders(
                self, album_id=album_id, filename=video_path.name, keep_asset_id=asset_id
            )
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            logger.warning("Could not supersede earlier renders: %s", exc)
        else:
            if superseded:
                logger.info(
                    "Superseded %d earlier upload(s) of the same recipe (moved to Immich trash)",
                    len(superseded),
                )

        return {"asset_id": asset_id, "album_id": album_id}


async def supersede_previous_renders(
    client,
    *,
    album_id: str | None,
    filename: str,
    keep_asset_id: str,
) -> list[str]:
    """Trash earlier uploads of the same recipe, keeping the one just uploaded.

    The filename carries a hash of the recipe -- the memory type, range, duration
    and the exact clips in order -- so an identical name means an identical edit
    and the older copy is superseded rather than kept beside it. Without this, a
    library accumulates one indistinguishable file per run; eight of them is what
    prompted this.

    Deliberately narrow. Only assets inside the album we just uploaded to, with
    exactly this filename, and never the asset we just created. An identically
    named file the user filed elsewhere is not ours to touch. Immich's trash is
    recoverable, so this is reversible by the user.
    """
    if not album_id:
        return []

    assets = await client.list_album_assets(album_id)
    superseded = [
        asset["id"]
        for asset in assets
        if asset.get("originalFileName") == filename and asset.get("id") != keep_asset_id
    ]
    if superseded:
        await client.trash_assets(superseded)
    return superseded
