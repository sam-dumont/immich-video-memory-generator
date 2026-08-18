"""Upload and album management API service."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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


class InvalidUploadResponse(ValueError):
    """Raised when Immich returns a malformed successful upload response."""


class AlbumNotFoundError(LookupError):
    """Raised when an album reference matches no album name or ID."""


class AmbiguousAlbumError(LookupError):
    """Raised when an album name matches more than one album.

    Immich libraries synced from iOS routinely carry several albums with the
    same name ('Récentes', 'Favorites'), so a name alone is not an identifier.
    """


@dataclass(frozen=True)
class AlbumRef:
    """An album resolved to its ID, display name and asset count."""

    id: str
    name: str
    asset_count: int


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


def _album_ref(album: dict) -> AlbumRef:
    return AlbumRef(
        id=album["id"],
        name=album.get("albumName") or album["id"],
        asset_count=album.get("assetCount") or 0,
    )


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

    async def resolve_album(self, name_or_id: str) -> AlbumRef:
        """Resolve an album reference to its ID, name and asset count.

        Matches an album ID first, then an exact name, then a case-insensitive
        name, so users can pass what they see in Immich.

        Raises:
            AmbiguousAlbumError: the name matches several albums.
            AlbumNotFoundError: nothing matches.
        """
        albums = await self.get_albums()

        by_id = {a.get("id"): a for a in albums}
        if name_or_id in by_id:
            return _album_ref(by_id[name_or_id])

        exact = [a for a in albums if a.get("albumName") == name_or_id]
        folded = name_or_id.casefold()
        matches = exact or [a for a in albums if (a.get("albumName") or "").casefold() == folded]

        if len(matches) == 1:
            return _album_ref(matches[0])
        if matches:
            raise AmbiguousAlbumError(
                f"{len(matches)} albums are named {name_or_id!r}. Pass the ID instead:\n"
                + "\n".join(
                    f"  {a['id']}  ({a.get('assetCount') or 0} assets)"
                    for a in sorted(matches, key=lambda a: -(a.get("assetCount") or 0))
                )
            )

        known = ", ".join(sorted({n for a in albums if (n := a.get("albumName"))})) or "none"
        raise AlbumNotFoundError(
            f"No Immich album named or with ID {name_or_id!r}. Albums: {known}"
        )

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
