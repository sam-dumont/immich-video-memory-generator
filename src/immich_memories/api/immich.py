"""Immich API client."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from immich_memories.api.models import (
    Asset,
    AssetType,
    MetadataSearchResult,
    Person,
    ServerInfo,
    TimeBucket,
    UserInfo,
)
from immich_memories.config import Config, get_config

if TYPE_CHECKING:
    from immich_memories.timeperiod import DateRange

logger = logging.getLogger(__name__)


class ImmichAPIError(Exception):
    """Base exception for Immich API errors."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ImmichAuthError(ImmichAPIError):
    """Authentication error with Immich API."""

    pass


class ImmichNotFoundError(ImmichAPIError):
    """Resource not found error."""

    pass


class ImmichClient:
    """Client for interacting with the Immich API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        config: Config | None = None,
        timeout: float = 30.0,
    ):
        """Initialize the Immich client.

        Args:
            base_url: Immich server URL. If not provided, uses config.
            api_key: Immich API key. If not provided, uses config.
            config: Configuration object. If not provided, uses global config.
            timeout: Request timeout in seconds.
        """
        if config is None:
            config = get_config()

        self.base_url = (base_url or config.immich.url).rstrip("/")
        self.api_key = api_key or config.immich.api_key
        self.timeout = timeout

        if not self.base_url:
            raise ValueError("Immich URL not configured")
        if not self.api_key:
            raise ValueError("Immich API key not configured")

        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "x-api-key": self.api_key,
                    "Accept": "application/json",
                },
                timeout=self.timeout,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> ImmichClient:
        """Async context manager entry."""
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object,
    ) -> None:
        """Async context manager exit."""
        await self.close()

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs,
    ) -> dict | list | bytes:
        """Make an API request.

        Args:
            method: HTTP method.
            endpoint: API endpoint (without /api prefix).
            **kwargs: Additional arguments for httpx.

        Returns:
            Response data (JSON or bytes).

        Raises:
            ImmichAPIError: If the request fails.
        """
        url = f"/api{endpoint}"
        logger.debug(f"Request: {method} {url}")

        try:
            response = await self.client.request(method, url, **kwargs)
        except httpx.TimeoutException as e:
            raise ImmichAPIError(f"Request timed out: {e}") from e
        except httpx.RequestError as e:
            raise ImmichAPIError(f"Request failed: {e}") from e

        if response.status_code == 401:
            raise ImmichAuthError("Invalid API key", status_code=401)
        elif response.status_code == 404:
            raise ImmichNotFoundError("Resource not found", status_code=404)
        elif response.status_code >= 400:
            try:
                error_data = response.json()
                message = error_data.get("message", response.text)
            except Exception:
                message = response.text
            raise ImmichAPIError(message, status_code=response.status_code)

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return response.content

    async def get_server_info(self) -> ServerInfo:
        """Get server version information."""
        data = await self._request("GET", "/server/version")
        return ServerInfo(**data)

    async def get_current_user(self) -> UserInfo:
        """Get current user information."""
        data = await self._request("GET", "/users/me")
        return UserInfo(**data)

    async def validate_connection(self) -> bool:
        """Validate the connection to Immich.

        Returns:
            True if connection is valid.

        Raises:
            ImmichAPIError: If connection fails.
        """
        try:
            await self.get_current_user()
            return True
        except ImmichAPIError:
            raise

    # Person-related endpoints

    async def get_all_people(self, with_hidden: bool = False) -> list[Person]:
        """Get all people from Immich.

        Args:
            with_hidden: Include hidden people.

        Returns:
            List of Person objects.
        """
        params = {"withHidden": str(with_hidden).lower()}
        data = await self._request("GET", "/people", params=params)

        # Handle both formats: {"people": [...]} or direct list
        people_data = data.get("people", []) if isinstance(data, dict) else data

        return [Person(**p) for p in people_data]

    async def get_person(self, person_id: str) -> Person:
        """Get a specific person by ID.

        Args:
            person_id: The person's ID.

        Returns:
            Person object.
        """
        data = await self._request("GET", f"/people/{person_id}")
        return Person(**data)

    async def get_person_by_name(self, name: str) -> Person | None:
        """Find a person by name.

        Args:
            name: Name to search for (case-insensitive).

        Returns:
            Person if found, None otherwise.
        """
        people = await self.get_all_people(with_hidden=True)
        name_lower = name.lower()
        for person in people:
            if person.name.lower() == name_lower:
                return person
        return None

    # Asset-related endpoints

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
        max_size_bytes: int = 10 * 1024**3,  # 10 GB default limit
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
                try:
                    if int(content_length) > max_size_bytes:
                        raise ValueError(
                            f"Asset {asset_id} size ({int(content_length)} bytes) "
                            f"exceeds limit ({max_size_bytes} bytes)"
                        )
                except (ValueError, OverflowError):
                    pass  # Invalid header, fall through to streaming check

            with open(output_path, "wb") as f:
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

    async def search_metadata(
        self,
        person_ids: list[str] | None = None,
        asset_type: AssetType | None = None,
        taken_after: datetime | None = None,
        taken_before: datetime | None = None,
        page: int = 1,
        size: int = 100,
    ) -> MetadataSearchResult:
        """Search assets by metadata.

        Args:
            person_ids: Filter by person IDs.
            asset_type: Filter by asset type.
            taken_after: Filter by date (inclusive).
            taken_before: Filter by date (inclusive).
            page: Page number (1-indexed).
            size: Page size.

        Returns:
            Search result with assets.
        """
        payload: dict = {
            "page": page,
            "size": size,
            "withExif": True,
            "withPeople": True,
        }

        if person_ids:
            payload["personIds"] = person_ids
        if asset_type:
            payload["type"] = asset_type.value
        if taken_after:
            payload["takenAfter"] = taken_after.isoformat()
        if taken_before:
            payload["takenBefore"] = taken_before.isoformat()

        data = await self._request("POST", "/search/metadata", json=payload)
        return MetadataSearchResult(**data)

    async def get_videos_for_person_and_year(
        self,
        person_id: str,
        year: int,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        """Get all videos for a specific person in a given year.

        Args:
            person_id: The person's ID.
            year: The year to filter by.
            progress_callback: Optional callback for progress updates.

        Returns:
            List of video assets.
        """
        taken_after = datetime(year, 1, 1, 0, 0, 0)
        taken_before = datetime(year, 12, 31, 23, 59, 59)

        all_assets: list[Asset] = []
        page = 1
        total_pages = None

        while True:
            result = await self.search_metadata(
                person_ids=[person_id],
                asset_type=AssetType.VIDEO,
                taken_after=taken_after,
                taken_before=taken_before,
                page=page,
                size=100,
            )

            assets = result.all_assets
            all_assets.extend(assets)

            if progress_callback:
                progress_callback(len(all_assets), total_pages)

            if not result.next_page:
                break

            page += 1

        # Sort by date
        all_assets.sort(key=lambda a: a.file_created_at)
        return all_assets

    async def get_all_videos_for_year(
        self,
        year: int,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        """Get all videos for a given year.

        Args:
            year: The year to filter by.
            progress_callback: Optional callback for progress updates.

        Returns:
            List of video assets.
        """
        taken_after = datetime(year, 1, 1, 0, 0, 0)
        taken_before = datetime(year, 12, 31, 23, 59, 59)

        all_assets: list[Asset] = []
        page = 1

        while True:
            result = await self.search_metadata(
                asset_type=AssetType.VIDEO,
                taken_after=taken_after,
                taken_before=taken_before,
                page=page,
                size=100,
            )

            assets = result.all_assets
            all_assets.extend(assets)

            if progress_callback:
                progress_callback(len(all_assets), None)

            if not result.next_page:
                break

            page += 1

        all_assets.sort(key=lambda a: a.file_created_at)
        return all_assets

    async def get_videos_for_date_range(
        self,
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        """Get all videos within a date range.

        Args:
            date_range: The date range to filter by.
            progress_callback: Optional callback for progress updates.

        Returns:
            List of video assets sorted by date.
        """
        all_assets: list[Asset] = []
        page = 1

        while True:
            result = await self.search_metadata(
                asset_type=AssetType.VIDEO,
                taken_after=date_range.start,
                taken_before=date_range.end,
                page=page,
                size=100,
            )

            assets = result.all_assets
            all_assets.extend(assets)

            if progress_callback:
                progress_callback(len(all_assets), None)

            if not result.next_page:
                break

            page += 1

        all_assets.sort(key=lambda a: a.file_created_at)
        return all_assets

    async def get_videos_for_person_and_date_range(
        self,
        person_id: str,
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        """Get all videos for a specific person within a date range.

        Args:
            person_id: The person's ID.
            date_range: The date range to filter by.
            progress_callback: Optional callback for progress updates.

        Returns:
            List of video assets sorted by date.
        """
        all_assets: list[Asset] = []
        page = 1

        while True:
            result = await self.search_metadata(
                person_ids=[person_id],
                asset_type=AssetType.VIDEO,
                taken_after=date_range.start,
                taken_before=date_range.end,
                page=page,
                size=100,
            )

            assets = result.all_assets
            all_assets.extend(assets)

            if progress_callback:
                progress_callback(len(all_assets), None)

            if not result.next_page:
                break

            page += 1

        all_assets.sort(key=lambda a: a.file_created_at)
        return all_assets

    async def iter_videos_for_date_range(
        self,
        date_range: DateRange,
        person_id: str | None = None,
        batch_size: int = 20,
    ) -> AsyncIterator[Asset]:
        """Iterate over videos within a date range.

        Args:
            date_range: The date range to filter by.
            person_id: Optional person ID to filter by.
            batch_size: Number of videos per batch.

        Yields:
            Asset objects.
        """
        page = 1
        person_ids = [person_id] if person_id else None

        while True:
            result = await self.search_metadata(
                person_ids=person_ids,
                asset_type=AssetType.VIDEO,
                taken_after=date_range.start,
                taken_before=date_range.end,
                page=page,
                size=batch_size,
            )

            for asset in result.all_assets:
                yield asset

            if not result.next_page:
                break

            page += 1

    # Time bucket endpoints

    async def get_time_buckets(
        self,
        size: str = "MONTH",
        asset_type: AssetType | None = None,
        person_id: str | None = None,
    ) -> list[TimeBucket]:
        """Get time buckets for timeline view.

        Args:
            size: Bucket size ("DAY", "MONTH").
            asset_type: Filter by asset type.
            person_id: Filter by person ID.

        Returns:
            List of time buckets.
        """
        params: dict = {"size": size}
        if asset_type:
            params["type"] = asset_type.value
        if person_id:
            params["personId"] = person_id

        data = await self._request("GET", "/timeline/buckets", params=params)
        return [TimeBucket(**b) for b in data]

    async def get_bucket_assets(
        self,
        bucket: str,
        size: str = "MONTH",
        asset_type: AssetType | None = None,
        person_id: str | None = None,
    ) -> list[Asset]:
        """Get assets for a specific time bucket.

        Args:
            bucket: Time bucket string (e.g., "2024-01-01T00:00:00.000Z").
            size: Bucket size ("DAY", "MONTH").
            asset_type: Filter by asset type.
            person_id: Filter by person ID.

        Returns:
            List of assets in the bucket.
        """
        params: dict = {
            "size": size,
            "timeBucket": bucket,
        }
        if asset_type:
            params["type"] = asset_type.value
        if person_id:
            params["personId"] = person_id

        data = await self._request("GET", "/timeline/bucket", params=params)
        return [Asset(**a) for a in data]

    # Utility methods

    async def get_available_years(
        self,
        person_id: str | None = None,
    ) -> list[int]:
        """Get list of years that have video content.

        Args:
            person_id: Optional person ID to filter by.

        Returns:
            Sorted list of years with videos.
        """
        buckets = await self.get_time_buckets(
            size="MONTH",
            asset_type=AssetType.VIDEO,
            person_id=person_id,
        )

        years = set()
        for bucket in buckets:
            # Parse year from bucket time string
            try:
                dt = datetime.fromisoformat(bucket.time_bucket.replace("Z", "+00:00"))
                years.add(dt.year)
            except (ValueError, AttributeError):
                continue

        return sorted(years, reverse=True)

    async def iter_person_videos(
        self,
        person_id: str,
        year: int,
        batch_size: int = 20,
    ) -> AsyncIterator[Asset]:
        """Iterate over videos for a person and year.

        Args:
            person_id: The person's ID.
            year: The year to filter by.
            batch_size: Number of videos per batch.

        Yields:
            Asset objects.
        """
        taken_after = datetime(year, 1, 1, 0, 0, 0)
        taken_before = datetime(year, 12, 31, 23, 59, 59)
        page = 1

        while True:
            result = await self.search_metadata(
                person_ids=[person_id],
                asset_type=AssetType.VIDEO,
                taken_after=taken_after,
                taken_before=taken_before,
                page=page,
                size=batch_size,
            )

            for asset in result.all_assets:
                yield asset

            if not result.next_page:
                break

            page += 1


# Synchronous wrapper for non-async contexts


class SyncImmichClient:
    """Synchronous wrapper for ImmichClient."""

    def __init__(self, *args, **kwargs):
        self._async_client = ImmichClient(*args, **kwargs)

    @property
    def base_url(self) -> str:
        """Get the base URL of the Immich server."""
        return self._async_client.base_url

    @property
    def api_key(self) -> str:
        """Get the API key."""
        return self._async_client.api_key

    @property
    def timeout(self) -> float:
        """Get the request timeout."""
        return self._async_client.timeout

    def _run(self, coro):
        """Run an async coroutine synchronously."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(coro)

    def close(self) -> None:
        """Close the client."""
        self._run(self._async_client.close())

    def __enter__(self) -> SyncImmichClient:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object,
    ) -> None:
        self.close()

    def validate_connection(self) -> bool:
        return self._run(self._async_client.validate_connection())

    def get_server_info(self) -> ServerInfo:
        return self._run(self._async_client.get_server_info())

    def get_current_user(self) -> UserInfo:
        return self._run(self._async_client.get_current_user())

    def get_all_people(self, with_hidden: bool = False) -> list[Person]:
        return self._run(self._async_client.get_all_people(with_hidden))

    def get_person(self, person_id: str) -> Person:
        return self._run(self._async_client.get_person(person_id))

    def get_person_by_name(self, name: str) -> Person | None:
        return self._run(self._async_client.get_person_by_name(name))

    def get_asset(self, asset_id: str) -> Asset:
        return self._run(self._async_client.get_asset(asset_id))

    def get_asset_thumbnail(self, asset_id: str, size: str = "preview") -> bytes:
        return self._run(self._async_client.get_asset_thumbnail(asset_id, size))

    def get_video_playback_url(self, asset_id: str) -> str:
        return self._async_client.get_video_playback_url(asset_id)

    def get_video_original_url(self, asset_id: str) -> str:
        return self._async_client.get_video_original_url(asset_id)

    def get_video_playback(self, asset_id: str) -> bytes:
        return self._run(self._async_client.get_video_playback(asset_id))

    def download_asset(self, asset_id: str, output_path: Path) -> Path:
        return self._run(self._async_client.download_asset(asset_id, output_path))

    def search_metadata(self, **kwargs) -> MetadataSearchResult:
        return self._run(self._async_client.search_metadata(**kwargs))

    def get_videos_for_person_and_year(
        self,
        person_id: str,
        year: int,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return self._run(
            self._async_client.get_videos_for_person_and_year(person_id, year, progress_callback)
        )

    def get_all_videos_for_year(
        self,
        year: int,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return self._run(self._async_client.get_all_videos_for_year(year, progress_callback))

    def get_time_buckets(self, **kwargs) -> list[TimeBucket]:
        return self._run(self._async_client.get_time_buckets(**kwargs))

    def get_bucket_assets(self, bucket: str, **kwargs) -> list[Asset]:
        return self._run(self._async_client.get_bucket_assets(bucket, **kwargs))

    def get_available_years(self, person_id: str | None = None) -> list[int]:
        return self._run(self._async_client.get_available_years(person_id))

    def get_videos_for_date_range(
        self,
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return self._run(
            self._async_client.get_videos_for_date_range(date_range, progress_callback)
        )

    def get_videos_for_person_and_date_range(
        self,
        person_id: str,
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return self._run(
            self._async_client.get_videos_for_person_and_date_range(
                person_id, date_range, progress_callback
            )
        )
