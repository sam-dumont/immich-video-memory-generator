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


def test_named_person_fetch_uses_filtered_endpoints_and_conserves_both_media():
    from types import SimpleNamespace

    from immich_memories.analysis.editorial_source import fetch_named_people_source

    window = DateRange(datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 12, 31, tzinfo=UTC))
    calls = []

    class Client:
        def get_all_people(self, with_hidden=False):
            assert with_hidden
            return [SimpleNamespace(id="person-id", name="Target")]

        def get_videos_for_person_and_date_range(self, person, dates):
            calls.append(("video", person, dates))
            return [make_asset("video", file_created_at=dates.start)]

        def get_photos_for_date_range(self, dates, person_id=None, person_ids=None):
            assert person_id == "person-id" and person_ids is None
            calls.append(("photo", person_id, dates))
            return [make_asset("photo", file_created_at=dates.start)]

        def get_videos_for_date_range(self, _dates):
            raise AssertionError("person fixture must not fetch unfiltered context")

    result = fetch_named_people_source(
        Client(), SourceScope(date_ranges=(window,)), names=["Target"]
    )
    assert [asset.id for asset in result] == ["photo", "video"]
    assert calls == [("video", "person-id", window), ("photo", "person-id", window)]


def test_same_named_face_identities_are_or_not_an_impossible_intersection():
    from types import SimpleNamespace

    from immich_memories.analysis.editorial_source import fetch_named_people_source

    window = DateRange(datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 12, 31, tzinfo=UTC))

    class Client:
        def get_all_people(self, with_hidden=False):
            return [
                SimpleNamespace(id="old-face", name="Target"),
                SimpleNamespace(id="new-face", name="Target"),
            ]

        def get_videos_for_person_and_date_range(self, person, dates):
            return [make_asset(person, file_created_at=dates.start)]

        def get_photos_for_date_range(self, dates, person_id=None, person_ids=None):
            assert person_id in {"old-face", "new-face"} and person_ids is None
            return []

        def get_videos_for_all_persons(self, *_args):
            raise AssertionError("merged face identities must be a union")

    result = fetch_named_people_source(
        Client(), SourceScope(date_ranges=(window,)), names=["Target"], person_match="and"
    )
    assert {asset.id for asset in result} == {"old-face", "new-face"}
