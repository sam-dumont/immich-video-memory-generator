"""Asset-type-agnostic query methods mixin.

These methods fetch ALL asset types (photos, videos, live photos) without
filtering by type. Used for trip detection where GPS data from any asset
type is valuable — especially for pre-2018 trips that only have photos.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from immich_memories.api.models import Asset, AssetType, MetadataSearchResult

if TYPE_CHECKING:
    from immich_memories.timeperiod import DateRange


class AllAssetsMixin:
    """Mixin providing asset-type-agnostic query methods for ImmichClient."""

    # Provided by SearchMixin via multiple inheritance
    async def search_metadata(  # type: ignore[empty-body]
        self,
        person_ids: list[str] | None = None,
        asset_type: AssetType | None = None,
        taken_after: datetime | None = None,
        taken_before: datetime | None = None,
        page: int = 1,
        size: int = 100,
    ) -> MetadataSearchResult: ...

    async def get_assets_for_date_range(
        self,
        date_range: DateRange,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> list[Asset]:
        """Get ALL assets (photos, videos, live photos) within a date range.

        Args:
            date_range: The date range to filter by.
            progress_callback: Optional callback for progress updates.

        Returns:
            List of all assets sorted by date.
        """
        all_assets: list[Asset] = []
        page = 1

        while True:
            result = await self.search_metadata(
                asset_type=None,
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

    async def get_assets_for_person_and_date_range(
        self,
        person_id: str,
        date_range: DateRange,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> list[Asset]:
        """Get ALL assets for a specific person within a date range."""
        all_assets: list[Asset] = []
        page = 1

        while True:
            result = await self.search_metadata(
                person_ids=[person_id],
                asset_type=None,
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

    async def get_assets_for_any_person(
        self,
        person_ids: list[str],
        date_range: DateRange,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> list[Asset]:
        """Get ALL assets containing ANY of the specified people (OR/union)."""
        if not person_ids:
            return []

        seen: dict[str, Asset] = {}
        for person_id in person_ids:
            assets = await self.get_assets_for_person_and_date_range(
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
