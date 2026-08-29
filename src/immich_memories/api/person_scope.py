"""Which Immich query a memory's people imply, whichever surface asked.

One rule, three endpoints. Naming one person narrows to them; naming nobody
takes the window whole. Several people are either an explicit intersection
(``and``: everybody appears in the asset) or union (``or``: anybody appears).
Both the CLI and wizard ask through here so the choice is made before Cull.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from immich_memories.timeperiod import DateRange


class VideoSource(Protocol):
    """The three video endpoints a windowed fetch can reach for."""

    def get_videos_for_all_persons(self, person_ids: list[str], date_range: DateRange) -> list: ...

    def get_videos_for_person_and_date_range(
        self, person_id: str, date_range: DateRange
    ) -> list: ...

    def get_videos_for_date_range(self, date_range: DateRange) -> list: ...


class PhotoSource(Protocol):
    """The photo endpoint a windowed fetch can reach."""

    def get_photos_for_date_range(
        self,
        date_range: DateRange,
        progress_callback: Callable[[int, int], None] | None = None,
        person_id: str | None = None,
        person_ids: list[str] | None = None,
    ) -> Sequence[Any]: ...


PersonMatch = Literal["and", "or"]


def _person_match(value: str) -> PersonMatch:
    if value == "and":
        return "and"
    if value == "or":
        return "or"
    raise ValueError(f"person_match must be 'and' or 'or', got {value!r}")


def _union_by_id(groups: Sequence[Sequence[Any]]) -> list[Any]:
    """Merge per-person API answers without offering one asset twice."""
    by_id = {asset.id: asset for group in groups for asset in group}
    return sorted(
        by_id.values(),
        key=lambda asset: (asset.file_created_at, asset.id),
    )


def videos_in_window(
    client: VideoSource,
    person_ids: list[str],
    date_range: DateRange,
    *,
    person_match: str = "and",
) -> list:
    """The videos one window holds, narrowed to the people the memory names."""
    match = _person_match(person_match)
    if len(person_ids) > 1:
        if match == "or":
            return _union_by_id(
                [
                    client.get_videos_for_person_and_date_range(person_id, date_range)
                    for person_id in person_ids
                ]
            )
        return client.get_videos_for_all_persons(person_ids, date_range)
    if len(person_ids) == 1:
        return client.get_videos_for_person_and_date_range(person_ids[0], date_range)
    return client.get_videos_for_date_range(date_range)


def photos_in_window(
    client: PhotoSource,
    person_ids: list[str],
    date_range: DateRange,
    *,
    person_match: str = "and",
) -> list:
    """The photos one window holds, with the same AND/OR rule as videos."""
    match = _person_match(person_match)
    if len(person_ids) > 1 and match == "or":
        return _union_by_id(
            [
                client.get_photos_for_date_range(date_range, person_id=person_id)
                for person_id in person_ids
            ]
        )
    person_id, group_ids = stills_person_args(person_ids)
    return list(
        client.get_photos_for_date_range(
            date_range,
            person_id=person_id,
            person_ids=group_ids,
        )
    )


def stills_person_args(person_ids: list[str]) -> tuple[str | None, list[str] | None]:
    """The ``person_id``/``person_ids`` pair the photo and Live Photo reads take.

    Both endpoints spell "one person" and "these people" as separate keyword
    arguments, so the choice between them is the same choice videos make and is
    made in the same place.
    """
    if len(person_ids) > 1:
        return None, person_ids
    if len(person_ids) == 1:
        return person_ids[0], None
    return None, None
