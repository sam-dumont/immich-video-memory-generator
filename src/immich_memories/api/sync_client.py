"""Synchronous wrapper for ImmichClient."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.api.models import (
    Asset,
    MetadataSearchResult,
    Person,
    ServerInfo,
    TimeBucket,
    UserInfo,
)

if TYPE_CHECKING:
    from immich_memories.timeperiod import DateRange


class SyncImmichClient:
    """Synchronous wrapper for ImmichClient."""

    def __init__(self, *args, **kwargs):
        from immich_memories.api.immich import ImmichClient

        self._async_client = ImmichClient(*args, **kwargs)
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def base_url(self) -> str:
        return self._async_client.base_url

    @property
    def api_key(self) -> str:
        return self._async_client.api_key

    @property
    def timeout(self) -> float:
        return self._async_client.timeout

    def _run(self, coro):
        """Run an async coroutine synchronously.

        Uses a persistent event loop so httpx's AsyncClient can reuse TCP
        connections across calls.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
            return self._loop.run_until_complete(coro)
        else:
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

    def get_photos_for_date_range(
        self,
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
        person_id: str | None = None,
    ) -> list[Asset]:
        return self._run(
            self._async_client.get_photos_for_date_range(
                date_range, progress_callback, person_id=person_id
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
