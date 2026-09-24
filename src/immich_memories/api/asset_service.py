"""Asset-related API service."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import httpx

from immich_memories.api.models import Asset, AssetFace

RequestFn = Callable[..., Any]
DEFAULT_DOWNLOAD_LIMIT = 25 * 1024**3

TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_TRANSFER_ATTEMPTS = 3
_BACKOFF_BASE = 1.0
_T = TypeVar("_T")

logger = logging.getLogger(__name__)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_STATUS
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError))


async def _retrying(label: str, attempt: Callable[[], Awaitable[_T]]) -> _T:
    """Run one idempotent GET, again after a transient failure, at most three times.

    A timeout, a dropped connection, 429 or a 5xx is worth another try; a 404
    or a size mismatch answers the same every time, so it raises at once.
    """
    for number in range(1, _TRANSFER_ATTEMPTS):
        try:
            return await attempt()
        except httpx.HTTPError as exc:
            if not _is_transient(exc):
                raise
            backoff = _BACKOFF_BASE * 2 ** (number - 1)
            logger.warning("%s failed (%s), retrying in %.0fs", label, type(exc).__name__, backoff)
            await asyncio.sleep(backoff)
    return await attempt()


def large_original_size(asset: Asset) -> int | None:
    """Bound an oversized original by its metadata, never by a Live Photo's still size."""
    if asset.live_photo_video_id:
        return None
    size = getattr(getattr(asset, "exif_info", None), "file_size_in_byte", None)
    return size if type(size) is int and size > DEFAULT_DOWNLOAD_LIMIT else None


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

    async def get_asset_faces(self, asset_id: str) -> list[AssetFace]:
        """Every face Immich found in one asset, with where each sits.

        The asset endpoint names the people it recognised but hands back no
        geometry for them, so the boxes have their own endpoint.
        """
        rows = await self._request("GET", "/faces", params={"id": asset_id})
        return [AssetFace(**row) for row in rows] if isinstance(rows, list) else []

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
        """The whole playback rendition, in memory.

        Sized for Live Photo companions (a few seconds); a full video goes to
        disk through `download_playback` instead.
        """
        return await self._request("GET", f"/assets/{asset_id}/video/playback")

    async def get_video_playback_range(
        self, asset_id: str, start: int, length: int
    ) -> tuple[bytes, int]:
        """``length`` bytes of the playback rendition from ``start``, and its full size.

        A server that ignores the range answers the whole rendition; the asked bytes are cut
        from it, so the caller sees the same answer either way. A refusal raises
        ``httpx.HTTPStatusError`` with the server's status.
        """

        async def attempt() -> httpx.Response:
            answer = await self._get_client().get(
                f"/api/assets/{asset_id}/video/playback",
                headers={"Range": f"bytes={start}-{start + length - 1}"},
            )
            answer.raise_for_status()
            return answer

        response = await _retrying(f"Playback range of {asset_id}", attempt)
        if response.status_code != 206:
            return response.content[start : start + length], len(response.content)
        return response.content, int(response.headers["content-range"].rpartition("/")[2])

    async def download_asset(
        self,
        asset_id: str,
        output_path: Path,
        max_size_bytes: int = DEFAULT_DOWNLOAD_LIMIT,
        *,
        expected_size_bytes: int | None = None,
    ) -> Path:
        """Download an original, bounded by its known size or the fallback limit.

        A caller with original-file metadata may supply an exact size above the
        fallback limit. Both oversized and incomplete transfers then fail.

        Raises:
            ValueError: If download exceeds its bound or differs from the expected size.
        """
        max_size_bytes = _download_bound(max_size_bytes, expected_size_bytes)
        return await self._stream_to(
            f"/api/assets/{asset_id}/original",
            asset_id,
            output_path,
            max_size_bytes,
            expected_size_bytes,
        )

    async def download_playback(self, asset_id: str, output_path: Path) -> Path:
        """Stream the video's playback rendition to a file, never holding it in memory."""
        return await self._stream_to(
            f"/api/assets/{asset_id}/video/playback",
            asset_id,
            output_path,
            DEFAULT_DOWNLOAD_LIMIT,
            None,
        )

    async def _stream_to(
        self,
        url: str,
        asset_id: str,
        output_path: Path,
        max_size_bytes: int,
        expected_size_bytes: int | None,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        async def attempt() -> Path:
            async with self._get_client().stream("GET", url) as response:
                response.raise_for_status()
                _check_content_length(
                    response.headers.get("content-length", ""),
                    asset_id,
                    max_size_bytes,
                    expected_size_bytes,
                )
                complete = False
                try:
                    await _write_bounded(
                        response, output_path, asset_id, max_size_bytes, expected_size_bytes
                    )
                    complete = True
                finally:
                    if not complete:
                        output_path.unlink(missing_ok=True)
            return output_path

        return await _retrying(f"Download of {asset_id}", attempt)


def _download_bound(max_size_bytes: int, expected_size_bytes: int | None) -> int:
    if expected_size_bytes is None:
        return max_size_bytes
    if type(expected_size_bytes) is not int or expected_size_bytes <= 0:
        raise ValueError("Expected original size must be a positive integer")
    return expected_size_bytes


def _check_content_length(
    header: str, asset_id: str, max_size_bytes: int, expected_size_bytes: int | None
) -> None:
    try:
        content_length = int(header)
    except (ValueError, OverflowError):
        return
    if content_length > max_size_bytes:
        raise ValueError(
            f"Asset {asset_id} size ({content_length} bytes) exceeds limit ({max_size_bytes} bytes)"
        )
    if expected_size_bytes is not None and content_length != expected_size_bytes:
        raise ValueError(f"Asset {asset_id} header differs from its expected size")


async def _write_bounded(
    response: httpx.Response,
    output_path: Path,
    asset_id: str,
    max_size_bytes: int,
    expected_size_bytes: int | None,
) -> None:
    bytes_downloaded = 0
    with output_path.open("wb") as f:
        async for chunk in response.aiter_bytes(chunk_size=1024**2):
            bytes_downloaded += len(chunk)
            if bytes_downloaded > max_size_bytes:
                raise ValueError(
                    f"Download for asset {asset_id} exceeded size limit ({max_size_bytes} bytes)"
                )
            f.write(chunk)
    if expected_size_bytes is not None and bytes_downloaded != expected_size_bytes:
        raise ValueError(f"Download for asset {asset_id} differs from its expected size")
