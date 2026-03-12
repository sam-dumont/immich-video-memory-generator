"""Search, video query, and time bucket API methods mixin."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import TYPE_CHECKING

from immich_memories.api.models import (
    Asset,
    AssetType,
    MetadataSearchResult,
    TimeBucket,
)

if TYPE_CHECKING:
    from immich_memories.timeperiod import DateRange


class SearchMixin:
    """Mixin providing search, video query, and time bucket methods for ImmichClient."""

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

    async def get_videos_for_any_person(
        self,
        person_ids: list[str],
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Asset]:
        """Get videos containing ANY of the specified people (OR query).

        Makes one query per person_id, deduplicates by asset ID, returns union sorted by date.
        """
        if not person_ids:
            return []

        seen: dict[str, Asset] = {}
        for person_id in person_ids:
            assets = await self.get_videos_for_person_and_date_range(
                person_id=person_id,
                date_range=date_range,
                progress_callback=progress_callback,
            )
            for asset in assets:
                if asset.id not in seen:
                    seen[asset.id] = asset

        result = list(seen.values())
        result.sort(key=lambda a: a.file_created_at)
        return result

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
