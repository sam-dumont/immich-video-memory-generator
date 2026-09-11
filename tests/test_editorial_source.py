"""Full editorial context honors exact windows without demand filtering."""

from __future__ import annotations

from datetime import UTC, datetime

from immich_memories.analysis.editorial_source import fetch_full_window_source
from immich_memories.analysis.selection_source import SourceScope
from immich_memories.timeperiod import DateRange
from tests.conftest import make_asset


def test_full_source_fetches_photos_and_videos_once_per_exact_unfiltered_window() -> None:
    first = DateRange(
        datetime(2020, 1, 1, tzinfo=UTC),
        datetime(2020, 1, 2, 23, 59, tzinfo=UTC),
    )
    second = DateRange(
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 2, 23, 59, tzinfo=UTC),
    )

    class Client:
        def __init__(self) -> None:
            self.video_windows = []
            self.photo_calls = []

        def get_videos_for_date_range(self, window):
            self.video_windows.append(window)
            return [make_asset(f"video-{window.start.year}", file_created_at=window.start)]

        def get_photos_for_date_range(
            self,
            window,
            progress_callback=None,
            person_id=None,
            person_ids=None,
        ):
            self.photo_calls.append((window, person_id, person_ids))
            return [
                make_asset(
                    f"photo-{window.start.year}",
                    file_created_at=window.start.replace(hour=1),
                )
            ]

        def get_videos_for_person_and_date_range(self, *_args):
            raise AssertionError("canonical context must not be person filtered")

        def get_videos_for_all_persons(self, *_args):
            raise AssertionError("canonical context must not be person filtered")

    client = Client()

    result = fetch_full_window_source(
        client,
        SourceScope(date_ranges=(first, second)),
    )

    assert client.video_windows == [first, second]
    assert client.photo_calls == [(first, None, None), (second, None, None)]
    assert [asset.id for asset in result] == [
        "video-2020",
        "photo-2020",
        "video-2026",
        "photo-2026",
    ]
