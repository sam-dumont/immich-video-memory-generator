"""Immich API client."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from pydantic import ValidationError

from immich_memories.api.client_album import AlbumMixin
from immich_memories.api.client_all_assets import AllAssetsMixin
from immich_memories.api.client_asset import AssetMixin
from immich_memories.api.client_person import PersonMixin
from immich_memories.api.client_search import SearchMixin
from immich_memories.api.models import (
    Asset,
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


class ImmichClient(AllAssetsMixin, AlbumMixin, AssetMixin, PersonMixin, SearchMixin):
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


# Synchronous wrapper for non-async contexts


class SyncImmichClient:
    """Synchronous wrapper for ImmichClient."""

    def __init__(self, *args, **kwargs):
        self._async_client = ImmichClient(*args, **kwargs)
        self._loop: asyncio.AbstractEventLoop | None = None

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
        """Run an async coroutine synchronously.

        Uses a persistent event loop so httpx's AsyncClient can reuse TCP
        connections across calls. asyncio.run() creates and destroys a loop
        each time, which breaks httpx's transport references on the 2nd call.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No event loop running — use persistent loop for connection reuse
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
            return self._loop.run_until_complete(coro)
        else:
            # Already inside an event loop (e.g., NiceGUI) — run in a thread
            import concurrent.futures

            def _run_with_persistent_loop():
                if self._loop is None or self._loop.is_closed():
                    self._loop = asyncio.new_event_loop()
                return self._loop.run_until_complete(coro)

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(_run_with_persistent_loop).result()

    def close(self) -> None:
        """Close the client and its event loop."""
        try:
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
            self._loop.run_until_complete(self._async_client.close())
        finally:
            if self._loop is not None and not self._loop.is_closed():
                self._loop.close()

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

    def get_assets_for_date_range(
        self,
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return self._run(
            self._async_client.get_assets_for_date_range(date_range, progress_callback)
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

    def get_live_photos_for_date_range(
        self,
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
        person_id: str | None = None,
        person_ids: list[str] | None = None,
    ) -> list[Asset]:
        return self._run(
            self._async_client.get_live_photos_for_date_range(
                date_range,
                progress_callback,
                person_id=person_id,
                person_ids=person_ids,
            )
        )

    def get_videos_for_any_person(
        self,
        person_ids: list[str],
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return self._run(
            self._async_client.get_videos_for_any_person(person_ids, date_range, progress_callback)
        )

    def get_assets_for_person_and_date_range(
        self,
        person_id: str,
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return self._run(
            self._async_client.get_assets_for_person_and_date_range(
                person_id, date_range, progress_callback
            )
        )

    def get_assets_for_any_person(
        self,
        person_ids: list[str],
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return self._run(
            self._async_client.get_assets_for_any_person(person_ids, date_range, progress_callback)
        )

    def get_videos_for_all_persons(
        self,
        person_ids: list[str],
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        return self._run(
            self._async_client.get_videos_for_all_persons(person_ids, date_range, progress_callback)
        )

    def upload_asset(self, file_path: Path) -> str:
        return self._run(self._async_client.upload_asset(file_path))

    def create_album(self, name: str, description: str | None = None) -> str:
        return self._run(self._async_client.create_album(name, description))

    def add_assets_to_album(self, album_id: str, asset_ids: list[str]) -> None:
        self._run(self._async_client.add_assets_to_album(album_id, asset_ids))

    def upload_memory(
        self, video_path: Path, album_name: str | None = None
    ) -> dict[str, str | None]:
        return self._run(self._async_client.upload_memory(video_path, album_name))
