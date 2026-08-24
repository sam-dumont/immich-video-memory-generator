"""Which Immich query a memory's people imply, whichever surface asked.

One rule, three endpoints. Naming several people asks for the moments holding
all of them rather than the union of their solo reels; naming one narrows to
them; naming nobody takes the window whole. The CLI resolved ``--person`` into
a list and branched on its length, while the wizard read a single pick and a
group pick out of two different state fields and branched on each separately --
so a group of one came out unfiltered on one surface and filtered on the other.
Both now ask through here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from immich_memories.timeperiod import DateRange


class VideoSource(Protocol):
    """The three video endpoints a windowed fetch can reach for."""

    def get_videos_for_all_persons(self, person_ids: list[str], date_range: DateRange) -> list: ...

    def get_videos_for_person_and_date_range(
        self, person_id: str, date_range: DateRange
    ) -> list: ...

    def get_videos_for_date_range(self, date_range: DateRange) -> list: ...


def videos_in_window(client: VideoSource, person_ids: list[str], date_range: DateRange) -> list:
    """The videos one window holds, narrowed to the people the memory names."""
    if len(person_ids) > 1:
        return client.get_videos_for_all_persons(person_ids, date_range)
    if len(person_ids) == 1:
        return client.get_videos_for_person_and_date_range(person_ids[0], date_range)
    return client.get_videos_for_date_range(date_range)


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
