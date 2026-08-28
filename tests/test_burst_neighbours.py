"""A person memory must still see a burst as motion.

Immich tags ONE frame of a burst with a person, not all of them. Fetching a
person's photographs therefore returns that frame alone, the burst has nothing
to stitch to, and it renders as a photograph — losing the motion the moment
actually had.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from immich_memories.analysis.live_photo_pipeline import (
    expand_to_neighbors,
    with_burst_neighbours,
)
from tests.conftest import make_asset

NOON = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)


def _live(index: int, *, seconds: float = 0.0):
    asset = make_asset(
        f"still-{index}", file_created_at=NOON + timedelta(seconds=seconds), duration=None
    )
    asset.live_photo_video_id = f"video-{index}"
    return asset


class TestATaggedFrameBringsItsBurst:
    def test_the_untagged_frames_of_a_tagged_burst_come_too(self):
        """Photos 1, 2 and 3 are one burst; only 2 carries the person tag."""
        burst = [_live(index, seconds=index * 2.0) for index in range(3)]
        tagged = [burst[1]]

        found = expand_to_neighbors(tagged, burst, merge_window_seconds=10.0)

        assert [a.id for a in found] == ["still-0", "still-1", "still-2"]

    def test_a_burst_nobody_tagged_stays_out(self):
        """Expansion follows a tag; it does not import the whole day."""
        here = [_live(0)]
        elsewhere = [_live(9, seconds=6000)]

        found = expand_to_neighbors(here, here + elsewhere, merge_window_seconds=10.0)

        assert [a.id for a in found] == ["still-0"]

    def test_the_fetch_only_asks_when_a_live_photo_was_tagged(self):
        """A person's plain photographs cost no extra request."""
        plain = make_asset("plain", file_created_at=NOON, duration=None)
        client = MagicMock()

        found = with_burst_neighbours(
            client, [plain], date_ranges=[MagicMock()], merge_window_seconds=10.0
        )

        assert found == [plain]
        client.get_live_photos_for_date_range.assert_not_called()

    def test_a_sparse_all_time_scope_only_queries_around_tagged_live_photos(self):
        """A 26-year person memory must not widen two burst lookups to the library."""
        from immich_memories.timeperiod import DateRange

        first = _live(1)
        second = _live(2, seconds=60 * 60 * 24 * 365)
        client = MagicMock()
        client.get_live_photos_for_date_range.return_value = []
        all_time = DateRange(NOON.replace(year=2000), NOON.replace(year=2026))

        with_burst_neighbours(
            client,
            [first, second],
            date_ranges=[all_time],
            merge_window_seconds=10.0,
        )

        asked = [call.args[0] for call in client.get_live_photos_for_date_range.call_args_list]
        assert len(asked) == 2
        assert all((window.end - window.start).total_seconds() == 20 for window in asked)

    def test_a_dense_all_time_scope_never_widens_to_the_whole_library(self):
        """Request count cannot turn a person scope into a 26-year Live Photo scan."""
        from immich_memories.timeperiod import DateRange

        tagged = [_live(index, seconds=index * 60 * 60 * 24) for index in range(40)]
        client = MagicMock()
        client.get_live_photos_for_date_range.return_value = []
        all_time = DateRange(NOON.replace(year=2000), NOON.replace(year=2026))

        with_burst_neighbours(
            client,
            tagged,
            date_ranges=[all_time],
            merge_window_seconds=10.0,
        )

        asked = [call.args[0] for call in client.get_live_photos_for_date_range.call_args_list]
        assert len(asked) == 40
        assert all((window.end - window.start).total_seconds() == 20 for window in asked)


class TestThePersonFetchBringsTheBurst:
    """The wiring, through the fetch the CLI actually calls."""

    def test_a_person_filtered_fetch_returns_the_whole_burst(self):
        """One tagged frame comes back; all three reach the pool.

        Without this a person memory's bursts stitch to the raw 3.0s, fall
        under the 3.5s threshold, and every one of them renders as a still.
        """
        from immich_memories.cli._asset_fetch import fetch_photos

        burst = [_live(index, seconds=index * 2.0) for index in range(3)]
        client = MagicMock()
        # WHY: the Immich server. Person search returns the tagged frame only;
        # the unfiltered Live Photo search returns the whole burst.
        client.get_photos_for_date_range.return_value = [burst[1]]
        client.get_live_photos_for_date_range.return_value = burst

        found = fetch_photos(
            client=client,
            date_ranges=[MagicMock()],
            person_ids=["person-1"],
            merge_window_seconds=10.0,
        )

        assert {a.id for a in found} == {"still-0", "still-1", "still-2"}

    def test_an_unfiltered_fetch_asks_for_nothing_extra(self):
        """With no person filter the window already holds every frame."""
        from immich_memories.cli._asset_fetch import fetch_photos

        burst = [_live(index, seconds=index * 2.0) for index in range(3)]
        client = MagicMock()
        client.get_photos_for_date_range.return_value = burst

        found = fetch_photos(client=client, date_ranges=[MagicMock()], person_ids=[])

        assert {a.id for a in found} == {"still-0", "still-1", "still-2"}
        client.get_live_photos_for_date_range.assert_not_called()
