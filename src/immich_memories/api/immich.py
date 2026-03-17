"""Immich API client."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from pydantic import ValidationError

from immich_memories.api.album_service import AlbumService
from immich_memories.api.all_assets_service import AllAssetsService
from immich_memories.api.asset_service import AssetService
from immich_memories.api.models import (
    Asset,
    AssetType,
    MetadataSearchResult,
    Person,
    ServerInfo,
    TimeBucket,
    UserInfo,
)
from immich_memories.api.person_service import PersonService
from immich_memories.api.search_service import SearchService
from immich_memories.config import Config

if TYPE_CHECKING:
    from immich_memories.timeperiod import DateRange

logger = logging.getLogger(__name__)


_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0


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
    """Client for interacting with the Immich API.

    Composes 5 services via constructor injection:
    - SearchService, AllAssetsService, AssetService, PersonService, AlbumService
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        config: Config | None = None,
        timeout: float = 30.0,
    ):
        if config is None:
            from immich_memories.config import get_config

            config = get_config()

        self.base_url = (base_url or config.immich.url).rstrip("/")
        self.api_key = api_key or config.immich.api_key
        self.timeout = timeout

        if not self.base_url:
            raise ValueError("Immich URL not configured")
        if not self.api_key:
            raise ValueError("Immich API key not configured")

        self._client: httpx.AsyncClient | None = None

        # Wire composed services
        self.search = SearchService(self._request)
        self.all_assets = AllAssetsService(self.search)
        self.assets = AssetService(self._request, self.base_url, lambda: self.client)
        self.people = PersonService(self._request)
        self.albums = AlbumService(self._request)

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
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object,
    ) -> None:
        await self.close()

    @staticmethod
    def _check_response(response: httpx.Response) -> dict | list | bytes:
        """Check response status and return parsed body, or raise on error."""
        if response.status_code == 401:
            raise ImmichAuthError("Invalid API key", status_code=401)
        if response.status_code == 404:
            raise ImmichNotFoundError("Resource not found", status_code=404)
        if response.status_code in _RETRYABLE_STATUS:
            raise ImmichAPIError(
                f"Server error: {response.status_code}",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
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

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Return True if the exception warrants a retry."""
        if isinstance(exc, (ImmichAuthError, ImmichNotFoundError)):
            return False
        return isinstance(exc, ImmichAPIError) and exc.status_code in _RETRYABLE_STATUS

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs,
    ) -> dict | list | bytes:
        """Make an API request with retry on transient failures.

        Retries up to _MAX_RETRIES times on timeout, network errors, and
        retryable status codes (429, 500-504). Non-retryable errors (401, 404,
        other 4xx) raise immediately.
        """
        url = f"/api{endpoint}"
        logger.debug(f"Request: {method} {url}")

        last_exception: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                response = await self.client.request(method, url, **kwargs)
                return self._check_response(response)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exception = ImmichAPIError(f"Request failed: {e}")
                last_exception.__cause__ = e
            except ImmichAPIError as e:
                if not self._is_retryable(e):
                    raise
                last_exception = e
            except httpx.RequestError as e:
                raise ImmichAPIError(f"Request failed: {e}") from e

            if attempt < _MAX_RETRIES - 1:
                backoff = _BACKOFF_BASE * (2**attempt)
                logger.warning(
                    f"{method} {url} attempt {attempt + 1} failed ({last_exception}), "
                    f"retrying in {backoff:.1f}s"
                )
                await asyncio.sleep(backoff)

        raise last_exception or ImmichAPIError("Request failed after retries")

    async def get_server_info(self) -> ServerInfo:
        """Get server version information."""
        data = await self._request("GET", "/server/version")
        try:
            return ServerInfo(**data)
        except ValidationError as e:
            raise ImmichAPIError(f"Unexpected API response format: {e}") from e

    async def get_current_user(self) -> UserInfo:
        """Get current user information."""
        data = await self._request("GET", "/users/me")
        try:
            return UserInfo(**data)
        except ValidationError as e:
            raise ImmichAPIError(f"Unexpected API response format: {e}") from e

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

    # ---- Delegate to SearchService ----

    async def search_metadata(
        self,
        person_ids: list[str] | None = None,
        asset_type: AssetType | None = None,
        taken_after: datetime | None = None,
        taken_before: datetime | None = None,
        page: int = 1,
        size: int = 100,
    ) -> MetadataSearchResult:
        return await self.search.search_metadata(
            person_ids=person_ids,
            asset_type=asset_type,
            taken_after=taken_after,
            taken_before=taken_before,
            page=page,
            size=size,
        )

    async def get_videos_for_person_and_year(
        self,
        person_id: str,
        year: int,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return await self.search.get_videos_for_person_and_year(person_id, year, progress_callback)

    async def get_all_videos_for_year(
        self,
        year: int,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return await self.search.get_all_videos_for_year(year, progress_callback)

    async def get_videos_for_date_range(
        self,
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return await self.search.get_videos_for_date_range(date_range, progress_callback)

    async def get_videos_for_person_and_date_range(
        self,
        person_id: str,
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return await self.search.get_videos_for_person_and_date_range(
            person_id, date_range, progress_callback
        )

    async def get_videos_for_any_person(
        self,
        person_ids: list[str],
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return await self.search.get_videos_for_any_person(
            person_ids, date_range, progress_callback
        )

    async def get_videos_for_all_persons(
        self,
        person_ids: list[str],
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return await self.search.get_videos_for_all_persons(
            person_ids, date_range, progress_callback
        )

    async def get_live_photos_for_date_range(
        self,
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
        person_id: str | None = None,
        person_ids: list[str] | None = None,
    ) -> list[Asset]:
        return await self.search.get_live_photos_for_date_range(
            date_range, progress_callback, person_id=person_id, person_ids=person_ids
        )

    async def iter_videos_for_date_range(
        self,
        date_range: DateRange,
        person_id: str | None = None,
        batch_size: int = 20,
    ) -> AsyncIterator[Asset]:
        async for asset in self.search.iter_videos_for_date_range(
            date_range, person_id, batch_size
        ):
            yield asset

    async def get_time_buckets(self, **kwargs) -> list[TimeBucket]:
        return await self.search.get_time_buckets(**kwargs)

    async def get_bucket_assets(self, bucket: str, **kwargs) -> list[Asset]:
        return await self.search.get_bucket_assets(bucket, **kwargs)

    async def get_available_years(self, person_id: str | None = None) -> list[int]:
        return await self.search.get_available_years(person_id)

    async def iter_person_videos(
        self, person_id: str, year: int, batch_size: int = 20
    ) -> AsyncIterator[Asset]:
        async for asset in self.search.iter_person_videos(person_id, year, batch_size):
            yield asset

    # ---- Delegate to AllAssetsService ----

    async def get_assets_for_date_range(
        self,
        date_range: DateRange,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> list[Asset]:
        return await self.all_assets.get_assets_for_date_range(date_range, progress_callback)

    async def get_assets_for_person_and_date_range(
        self,
        person_id: str,
        date_range: DateRange,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> list[Asset]:
        return await self.all_assets.get_assets_for_person_and_date_range(
            person_id, date_range, progress_callback
        )

    async def get_assets_for_any_person(
        self,
        person_ids: list[str],
        date_range: DateRange,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> list[Asset]:
        return await self.all_assets.get_assets_for_any_person(
            person_ids, date_range, progress_callback
        )

    # ---- Delegate to AssetService ----

    async def get_asset(self, asset_id: str) -> Asset:
        return await self.assets.get_asset(asset_id)

    async def get_asset_thumbnail(self, asset_id: str, size: str = "preview") -> bytes:
        return await self.assets.get_asset_thumbnail(asset_id, size)

    def get_video_playback_url(self, asset_id: str) -> str:
        return self.assets.get_video_playback_url(asset_id)

    def get_video_original_url(self, asset_id: str) -> str:
        return self.assets.get_video_original_url(asset_id)

    async def get_video_playback(self, asset_id: str) -> bytes:
        return await self.assets.get_video_playback(asset_id)

    async def download_asset(self, asset_id: str, output_path: Path) -> Path:
        return await self.assets.download_asset(asset_id, output_path)

    # ---- Delegate to PersonService ----

    async def get_all_people(self, with_hidden: bool = False) -> list[Person]:
        return await self.people.get_all_people(with_hidden)

    async def get_person(self, person_id: str) -> Person:
        return await self.people.get_person(person_id)

    async def get_person_by_name(self, name: str) -> Person | None:
        return await self.people.get_person_by_name(name)

    # ---- Delegate to AlbumService ----

    async def get_albums(self) -> list[dict]:
        return await self.albums.get_albums()

    async def find_album_by_name(self, name: str) -> str | None:
        return await self.albums.find_album_by_name(name)

    async def upload_asset(self, file_path: Path) -> str:
        return await self.albums.upload_asset(file_path)

    async def create_album(self, name: str, description: str | None = None) -> str:
        return await self.albums.create_album(name, description)

    async def add_assets_to_album(self, album_id: str, asset_ids: list[str]) -> None:
        await self.albums.add_assets_to_album(album_id, asset_ids)

    async def upload_memory(
        self, video_path: Path, album_name: str | None = None
    ) -> dict[str, str | None]:
        return await self.albums.upload_memory(video_path, album_name)


# Re-export SyncImmichClient — bottom import avoids circular dependency
from immich_memories.api.sync_client import SyncImmichClient  # noqa: E402

__all__ = [
    "ImmichAPIError",
    "ImmichAuthError",
    "ImmichClient",
    "ImmichNotFoundError",
    "SyncImmichClient",
]
