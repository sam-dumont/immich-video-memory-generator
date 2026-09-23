"""A model-tier film that stays short reads a few unread episodes of its shot-less stories.

On a cold library the notable records come only from readings, and readings only from the draft's
own episodes, so a story the draft never reached could never earn a newcomer seat. When the cut is
short by S seconds, the polish reads at most 2 * ceil(S / 3.5) of those stories' unread episodes,
the cheapest signals first, and seats a story only when its reading recorded something. A story
whose reading records nothing stays out: short beats a guess.
"""

from __future__ import annotations

import math
from datetime import timedelta

from immich_memories.analysis.editorial_thin_short import ShortReads
from tests.editorial_thin_fixtures import START, Film, polish, thin_budget


class Library:
    """# WHY: the episode reader and its bank sit behind the product's text model; the test
    states which episodes exist and what reading each one records."""

    def __init__(self, episodes: dict[str, list[str]], records: dict[str, str]) -> None:
        self.episodes = episodes
        self.records = records
        self.read: list[str] = []

    def unread(self, assets):
        wanted = set(assets)
        return {
            key: [asset for asset in members if asset in wanted]
            for key, members in self.episodes.items()
            if key not in self.read and wanted & set(members)
        }

    def records_for(self, assets):
        wanted = set(assets)
        keys = [key for key, members in self.episodes.items() if wanted & set(members)]
        self.read.extend(key for key in keys if key not in self.read)
        covered = {asset for key in keys for asset in self.episodes[key]}
        return {asset: why for asset, why in self.records.items() if asset in covered}

    def port(self) -> ShortReads:
        return ShortReads(unread=self.unread, records=self.records_for, standing=lambda _a: 1)


def april_shaped() -> Film:
    """Four shots in the first week, and two later weeks the draft never reached."""
    film = Film()
    film.story("S000", "maybe", 6, START)
    for number in range(4):
        film.draft.append(
            film.shot(f"d{number}", "S000", START + timedelta(hours=number), f"people {number}")
        )
    film.story("S001", "maybe", 6, START + timedelta(days=8))
    film.story("S002", "maybe", 6, START + timedelta(days=15))
    return film


def test_an_undrafted_week_with_a_notable_record_gets_one_seat_and_the_other_stays_empty(
    tmp_path,
):
    film = april_shaped()
    library = Library(
        {
            "E1": [f"S001-c{n:04d}" for n in range(6)],
            "E2": [f"S002-c{n:04d}" for n in range(6)],
        },
        {"S001-c0003": "the first time on a bike"},
    )

    judge, payload, _cut, newcomers = polish(tmp_path, film, short=library.port(), room=44.0)

    # the week whose reading recorded a moment gets its one seat; the other week stays empty
    assert len(newcomers) == 1 and newcomers[0].startswith("S001-")
    assert sorted(library.read) == ["E1", "E2"]
    short = payload["short"]
    assert short["episodes_read"] == 2 and short["seats"] == 1
    assert len(judge.calls) <= thin_budget(4, 0) + math.ceil(2 / 3) + 4 * 1


def test_a_film_short_by_seven_seconds_reads_four_episodes_and_the_uncovered_weeks_first(
    tmp_path,
):
    film = april_shaped()
    episodes = {}
    for number in range(3, 10):
        # three more stories in the draft's own week (on days it has no shot of), four later
        offset = timedelta(days=number - 2) if number < 6 else timedelta(days=7 * (number - 4))
        key = f"S{number:03d}"
        film.story(key, "maybe", 3, START + offset)
        episodes[f"E{number}"] = [f"{key}-c{n:04d}" for n in range(3)]
    library = Library(episodes, {})

    _judge, payload, _cut, newcomers = polish(tmp_path, film, short=library.port(), room=7.0)

    assert sorted(library.read) == ["E6", "E7", "E8", "E9"]
    assert payload["short"]["episodes_read"] == 4
    # nothing recorded, nothing seated
    assert newcomers == [] and payload["short"]["seats"] == 0


def test_a_film_that_is_not_short_reads_nothing_more(tmp_path):
    film = april_shaped()
    library = Library({"E1": [f"S001-c{n:04d}" for n in range(6)]}, {"S001-c0001": "a first"})

    _judge, payload, _cut, newcomers = polish(tmp_path, film, short=library.port(), room=3.0)

    assert library.read == [] and newcomers == []
    assert payload["short"] == {}
