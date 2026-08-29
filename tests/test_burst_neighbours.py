"""A person memory keeps the Immich person tag as its exact source boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from tests.conftest import make_asset

NOON = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)


def _live(index: int, *, seconds: float = 0.0):
    asset = make_asset(
        f"still-{index}", file_created_at=NOON + timedelta(seconds=seconds), duration=None
    )
    asset.live_photo_video_id = f"video-{index}"
    return asset


class TestThePersonFetchDoesNotWidenThePool:
    """The person tag is the production source boundary."""

    def test_a_person_filtered_fetch_returns_only_the_tagged_live_photo(self):
        """Temporal neighbours without the person tag do not enter the pool."""
        from immich_memories.cli._asset_fetch import fetch_photos

        burst = [_live(index, seconds=index * 2.0) for index in range(3)]
        client = MagicMock()
        # WHY: the Immich server. Person search returns the tagged frame only.
        client.get_photos_for_date_range.return_value = [burst[1]]
        client.get_live_photos_for_date_range.return_value = burst

        found = fetch_photos(
            client=client,
            date_ranges=[MagicMock()],
            person_ids=["person-1"],
        )

        assert [asset.id for asset in found] == ["still-1"]
        client.get_live_photos_for_date_range.assert_not_called()

    def test_an_unfiltered_fetch_asks_for_nothing_extra(self):
        """With no person filter the window already holds every frame."""
        from immich_memories.cli._asset_fetch import fetch_photos

        burst = [_live(index, seconds=index * 2.0) for index in range(3)]
        client = MagicMock()
        client.get_photos_for_date_range.return_value = burst

        found = fetch_photos(client=client, date_ranges=[MagicMock()], person_ids=[])

        assert {a.id for a in found} == {"still-0", "still-1", "still-2"}
        client.get_live_photos_for_date_range.assert_not_called()
