"""Asset-related API methods mixin."""

from __future__ import annotations

import contextlib
from pathlib import Path

from immich_memories.api.models import Asset


class AssetMixin:
    """Mixin providing asset-related API methods for ImmichClient."""

    async def get_asset(self, asset_id: str) -> Asset:
        """Get a specific asset by ID.

        Args:
            asset_id: The asset's ID.

        Returns:
            Asset object.
        """
        data = await self._request("GET", f"/assets/{asset_id}")
        return Asset(**data)

    async def get_asset_thumbnail(
        self,
        asset_id: str,
        size: str = "preview",
    ) -> bytes:
        """Get asset thumbnail.

        Args:
            asset_id: The asset's ID.
            size: Thumbnail size ("thumbnail" or "preview").

        Returns:
            Thumbnail image bytes.
        """
        params = {"size": size}
        return await self._request("GET", f"/assets/{asset_id}/thumbnail", params=params)

    def get_video_playback_url(self, asset_id: str) -> str:
        """Get the video playback URL for streaming/preview.

        Args:
            asset_id: The asset's ID.

        Returns:
            Full URL for video playback (transcoded preview).
        """
        return f"{self.base_url}/api/assets/{asset_id}/video/playback"

    def get_video_original_url(self, asset_id: str) -> str:
        """Get the URL for the original video file.

        Args:
            asset_id: The asset's ID.

        Returns:
            Full URL for original video (preserves HDR metadata).
        """
        return f"{self.base_url}/api/assets/{asset_id}/original"

    async def get_video_playback(self, asset_id: str) -> bytes:
        """Get video playback data (transcoded preview).

        Args:
            asset_id: The asset's ID.

        Returns:
            Video bytes (transcoded/preview quality).
        """
        return await self._request("GET", f"/assets/{asset_id}/video/playback")

    async def download_asset(
        self,
        asset_id: str,
        output_path: Path,
        max_size_bytes: int = 25 * 1024**3,  # 25 GB default limit (4K HDR ~2.3 GB/min)
    ) -> Path:
        """Download an asset's original file.

        Args:
            asset_id: The asset's ID.
            output_path: Path to save the file.
            max_size_bytes: Maximum allowed download size in bytes.

        Returns:
            Path to the downloaded file.

        Raises:
            ValueError: If download exceeds size limit.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        bytes_downloaded = 0

        async with self.client.stream(
            "GET",
            f"/api/assets/{asset_id}/original",
        ) as response:
            response.raise_for_status()

            # Fast-fail on Content-Length if available
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
