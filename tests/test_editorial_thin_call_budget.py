"""The thin layer's calls grow with the seats it fills, never with the draft or its stories.

A counting judge answers every question the layer asks, through the production standing gate,
audience gate, vote, picker and refill. The budget is the owner's: one look at the draft costs
four questions per twelve shots (standing and fit once, audience in two orders), and each seat
costs at most four more.
A seat whose story holds a thousand pictures costs what a seat in a small story costs.
"""

from __future__ import annotations

from datetime import timedelta

from tests.editorial_thin_fixtures import (
    DOUBTFUL,
    JUNK,
    PRIVATE,
    START,
    UNSTEADY,
    Film,
    draft_of,
    polish,
    thin_budget,
)


def test_three_seats_in_stories_of_a_thousand_pictures_stay_inside_the_budget(tmp_path):
    film = Film()
    draft_of(film, 160, flagged={10: JUNK, 80: UNSTEADY, 150: PRIVATE})

    judge, payload, cut, newcomers = polish(tmp_path, film)

    kept = {row["asset_id"] for row in cut}
    assert not {"d010", "d080", "d150"} & kept
    assert len(payload["slots"]) == 3
    assert len(newcomers) == 3
    assert len(judge.calls) <= thin_budget(160, 3)


def test_a_draft_nothing_is_wrong_with_costs_one_look(tmp_path):
    film = Film()
    draft_of(film, 160, flagged={})

    judge, payload, cut, _newcomers = polish(tmp_path, film)

    assert len(cut) == 160
    assert payload["slots"] == []
    assert len(judge.calls) <= thin_budget(160, 0)


def year_shaped(film: Film) -> None:
    """A year: 161 shots over forty stories of every tier, and 25 seats of every kind.

    Some of the stories a seat opens in lead their pages with pictures the standing gate
    refuses, so a seat has to pick again.
    """
    tiers = ("remarkable", "maybe", "background")
    flagged = {
        **dict.fromkeys(range(3, 160, 32), JUNK),
        **dict.fromkeys(range(7, 160, 32), DOUBTFUL),
        **dict.fromkeys(range(11, 160, 16), UNSTEADY),
        **dict.fromkeys(range(13, 160, 32), PRIVATE),
    }
    for index in range(161):
        story = f"S{index // 4:03d}"
        when = START + timedelta(hours=54 * index)
        if story not in film.tiers:
            film.story(
                story,
                tiers[(index // 4) % 3],
                400 if index // 4 % 2 else 60,
                when + timedelta(days=1),
                caption_of=lambda n: UNSTEADY if n < 2 else "the family together",
            )
        caption = flagged.get(index, f"people at a table, shot {index}")
        film.draft.append(
            film.shot(f"d{index:03d}", story, when, caption, favourite=index % 9 == 0)
        )
    film.draft.sort(key=lambda row: (row["taken"], row["asset_id"]))


def test_a_year_shaped_draft_spends_calls_on_its_seats_not_its_size(tmp_path):
    film = Film()
    year_shaped(film)

    judge, payload, _cut, newcomers = polish(tmp_path, film)

    seats = len(payload["slots"])
    assert seats >= 20
    assert newcomers
    assert len(judge.calls) <= thin_budget(161, seats)
