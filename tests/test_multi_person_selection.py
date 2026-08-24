"""A multi-person memory is about the people who were there together.

Naming two people asks for the moments that hold both of them, not the union of
two solo reels. These tests pin that rule at the fetch seam the CLI uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from immich_memories.cli._pipeline_runner import fetch_photos, fetch_videos_and_live_photos
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange

PERSON_A = "person-a"
PERSON_B = "person-b"

WINDOW = DateRange(start=datetime(2025, 1, 1), end=datetime(2025, 12, 31, 23, 59, 59))


@dataclass
class _Asset:
    id: str
    people: set[str] = field(default_factory=set)
    file_created_at: datetime = datetime(2025, 6, 1, 12, 0, 0)
    duration_seconds: float = 8.0


class _LibraryClient:
    """A library that answers person queries the way the Immich client does.

    Every answer is derived from the same per-person lookup, so a test cannot
    accidentally describe a union as an intersection.
    """

    def __init__(self, videos: list[_Asset], photos: list[_Asset] | None = None) -> None:
        self._videos = videos
        self._photos = photos or []

    def get_photos_for_date_range(
        self,
        date_range: DateRange,  # noqa: ARG002
        person_id: str | None = None,
        person_ids: list[str] | None = None,
    ) -> list[_Asset]:
        wanted = set(person_ids or []) | ({person_id} if person_id else set())
        return [p for p in self._photos if wanted <= p.people]

    def get_videos_for_date_range(self, date_range: DateRange) -> list[_Asset]:  # noqa: ARG002
        return list(self._videos)

    def get_videos_for_person_and_date_range(
        self,
        person_id: str,
        date_range: DateRange,  # noqa: ARG002
    ) -> list[_Asset]:
        return [a for a in self._videos if person_id in a.people]

    def get_videos_for_all_persons(
        self, person_ids: list[str], date_range: DateRange
    ) -> list[_Asset]:
        per_person = [
            {a.id for a in self.get_videos_for_person_and_date_range(p, date_range)}
            for p in person_ids
        ]
        common = set.intersection(*per_person) if per_person else set()
        return [a for a in self._videos if a.id in common]


class _SilentProgress:
    def add_task(self, *_args, **_kwargs) -> int:
        return 0

    def update(self, *_args, **_kwargs) -> None:
        return None


def _fetch(client: _LibraryClient, person_ids: list[str]) -> list[_Asset]:
    assets, _live = fetch_videos_and_live_photos(
        client=client,
        config=Config(),
        progress=_SilentProgress(),
        date_ranges=[WINDOW],
        person_ids=person_ids,
        use_live_photos=False,
    )
    return assets


def test_two_people_selects_only_the_moments_that_hold_both():
    client = _LibraryClient(
        [
            _Asset("a-alone", {PERSON_A}),
            _Asset("b-alone", {PERSON_B}),
            _Asset("a-and-b", {PERSON_A, PERSON_B}),
        ]
    )

    assets = _fetch(client, [PERSON_A, PERSON_B])

    assert [a.id for a in assets] == ["a-and-b"]


def test_no_shared_moment_yields_nothing_rather_than_two_solo_reels():
    client = _LibraryClient([_Asset("a-alone", {PERSON_A}), _Asset("b-alone", {PERSON_B})])

    assets = _fetch(client, [PERSON_A, PERSON_B])

    assert assets == []


def test_photos_follow_the_same_rule_as_videos():
    client = _LibraryClient(
        [],
        photos=[
            _Asset("photo-a-alone", {PERSON_A}),
            _Asset("photo-nobody-named"),
            _Asset("photo-a-and-b", {PERSON_A, PERSON_B}),
        ],
    )

    photos = fetch_photos(client=client, date_ranges=[WINDOW], person_ids=[PERSON_A, PERSON_B])

    assert [p.id for p in photos] == ["photo-a-and-b"]


def test_one_person_still_selects_every_moment_they_appear_in():
    client = _LibraryClient(
        [
            _Asset("a-alone", {PERSON_A}),
            _Asset("b-alone", {PERSON_B}),
            _Asset("a-and-b", {PERSON_A, PERSON_B}),
        ]
    )

    assets = _fetch(client, [PERSON_A])

    assert {a.id for a in assets} == {"a-alone", "a-and-b"}
