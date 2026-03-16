"""Asset-related API service."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from immich_memories.api.models import Asset

RequestFn = Callable[..., Any]


class AssetService:
    """Asset retrieval and download operations against the Immich API."""

    def __init__(
        self,
        request_fn: RequestFn,
        base_url: str,
        get_client: Callable[[], httpx.AsyncClient],
    ) -> None:
        self._request = request_fn
        self._base_url = base_url
        self._get_client = get_client

    async def get_asset(self, asset_id: str) -> Asset:
        """Get a specific asset by ID."""
        data = await self._request("GET", f"/assets/{asset_id}")
        return Asset(**data)

    async def get_asset_thumbnail(self, asset_id: str, size: str = "preview") -> bytes:
        """Get asset thumbnail."""
        params = {"size": size}
        return await self._request("GET", f"/assets/{asset_id}/thumbnail", params=params)

    def get_video_playback_url(self, asset_id: str) -> str:
        """Get the video playback URL for streaming/preview."""
        return f"{self._base_url}/api/assets/{asset_id}/video/playback"

    def get_video_original_url(self, asset_id: str) -> str:
        """Get the URL for the original video file."""
        return f"{self._base_url}/api/assets/{asset_id}/original"

    async def get_video_playback(self, asset_id: str) -> bytes:
        """Get video playback data (transcoded preview)."""
        return await self._request("GET", f"/assets/{asset_id}/video/playback")

    async def download_asset(
        self,
        asset_id: str,
        output_path: Path,
        max_size_bytes: int = 25 * 1024**3,
    ) -> Path:
        """Download an asset's original file.

        Raises:
            ValueError: If download exceeds size limit.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        bytes_downloaded = 0

        async with self._get_client().stream(
            "GET",
            f"/api/assets/{asset_id}/original",
        ) as response:
            response.raise_for_status()

            content_length = response.headers.get("content-length")
            if content_length:
                with contextlib.suppress(ValueError, OverflowError):
                    if int(content_length) > max_size_bytes:
                        raise ValueError(
                            f"Asset {asset_id} size ({int(content_length)} bytes) "
                            f"exceeds limit ({max_size_bytes} bytes)"
                        )

            with output_path.open("wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    bytes_downloaded += len(chunk)
                    if bytes_downloaded > max_size_bytes:
                        f.close()
                        output_path.unlink(missing_ok=True)
                        raise ValueError(
                            f"Download for asset {asset_id} exceeded size limit "
                            f"({max_size_bytes} bytes)"
                        )
                    f.write(chunk)

        return output_path
