"""Re-rendering the same recipe replaces the old upload instead of stacking."""

from __future__ import annotations

import pytest

from immich_memories.api.album_service import supersede_previous_renders


class _Recorder:
    """Stands in for the Immich album/asset endpoints."""

    def __init__(self, existing: list[dict]) -> None:
        self.existing = existing
        self.trashed: list[list[str]] = []

    async def list_album_assets(self, album_id: str) -> list[dict]:
        return self.existing

    async def trash_assets(self, asset_ids: list[str]) -> None:
        self.trashed.append(asset_ids)

    async def generated_asset_ids(self) -> frozenset[str]:
        return frozenset()


@pytest.mark.asyncio
async def test_a_matching_filename_does_not_authorize_trashing_an_original() -> None:
    """A custom output name can collide with a video the user shot themselves."""
    # WHY: records the trash call without touching a real photo library.
    client = _Recorder(
        [
            {"id": "original", "originalFileName": "holiday.mp4", "deviceId": "phone"},
            # No device identity and no provenance tag: authorship cannot be proven.
            {"id": "unknown-origin", "originalFileName": "holiday.mp4"},
            {"id": "new", "originalFileName": "holiday.mp4"},
        ]
    )

    superseded = await supersede_previous_renders(
        client, album_id="alb", filename="holiday.mp4", keep_asset_id="new"
    )

    assert superseded == []
    assert client.trashed == []


@pytest.mark.asyncio
async def test_an_older_render_of_the_same_recipe_is_trashed() -> None:
    client = _Recorder(
        [
            {
                "id": "old",
                "originalFileName": "june_a1b2c3d4.mp4",
                "deviceId": "immich-memories",
                "deviceAssetId": "immich-memories-a1b2c3d4",
            },
            {"id": "new", "originalFileName": "june_a1b2c3d4.mp4"},
            {"id": "other-recipe", "originalFileName": "june_9f8e7d6c.mp4"},
            {"id": "unrelated", "originalFileName": "holiday.mp4"},
        ]
    )

    await supersede_previous_renders(
        client, album_id="alb", filename="june_a1b2c3d4.mp4", keep_asset_id="new"
    )

    assert client.trashed == [["old"]]


@pytest.mark.asyncio
async def test_nothing_is_trashed_when_the_recipe_is_new() -> None:
    client = _Recorder([{"id": "new", "originalFileName": "june_a1b2c3d4.mp4"}])

    await supersede_previous_renders(
        client, album_id="alb", filename="june_a1b2c3d4.mp4", keep_asset_id="new"
    )

    assert client.trashed == []


@pytest.mark.asyncio
async def test_a_render_outside_the_album_is_left_alone() -> None:
    """Scope is the album we uploaded into. An identically named asset the user
    filed somewhere else is not ours to delete."""
    client = _Recorder([{"id": "new", "originalFileName": "june_a1b2c3d4.mp4"}])

    await supersede_previous_renders(
        client, album_id=None, filename="june_a1b2c3d4.mp4", keep_asset_id="new"
    )

    assert client.trashed == []
