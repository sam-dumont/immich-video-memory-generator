"""The polish keeps the promises the draft made: every year a voice, the favourite its moment.

Measured on the lifetime person film (09-24): the vote removed a year's only shot six times in
one run, and a seat chose a non-favourite over the favourite of the same moment.
"""

from __future__ import annotations

from immich_memories.analysis.editorial_thin_gates import ThinGates
from immich_memories.analysis.editorial_thin_layer import ThinPolish
from tests.test_editorial_thin_vote_relations import STORY, Audience, FitJudge, Standing


def year_of(taken: str) -> str:
    return f"year-{taken[:4]}"


def carrier(asset: str, taken: str, **extra) -> dict:
    return {
        "asset_id": asset,
        "story_episode": "S001",
        "taken": taken,
        "moment": f"m-{asset}",
        "seconds": 4.0,
        "kind": "still",
        "favourite": False,
        **extra,
    }


def polish_years(tmp_path, shots, *, era_of=year_of, record=lambda _n, _p: None):
    """`shots` maps asset -> (taken, line); a line containing `filler` is named by both orders.

    # WHY: FitJudge stands in for the model's thesis-fit vote, the boundary under test.
    """
    polish = ThinPolish(
        bank_dir=tmp_path,
        read_period=lambda _stories: ("A life, year by year.", {}),
    )
    cut = [carrier(asset, taken) for asset, (taken, _line) in shots.items()]
    lines = {asset: line for asset, (_taken, line) in shots.items()}
    kept = polish.polish(
        cut,
        judge=FitJudge(),
        gates=ThinGates(Standing(), Audience(), thumbnail_hash=lambda _a: None),
        catalogue=polish.catalogue_of(STORY, {"m1": list(shots)}, drafted=cut),
        contract="contract",
        line_of=lines.get,
        record=record,
        era_of=era_of,
    )
    return [c["asset_id"] for c in kept]


def test_a_years_only_shot_survives_a_vote_that_names_it(tmp_path):
    written: dict[str, dict] = {}
    kept = polish_years(
        tmp_path,
        {
            "a1": ("2006-05-01T09:00:00", "a child on a swing"),
            "a2": ("2007-05-01T09:00:00", "filler: a child with a toy"),
            "a3": ("2008-05-01T09:00:00", "a child at the beach"),
            "a4": ("2008-06-01T09:00:00", "filler: a child at a table"),
        },
        record=lambda name, payload: written.__setitem__(name, dict(payload)),
    )

    assert kept == ["a1", "a2", "a3"]
    verdict = written["thin-polish"]["verdicts"]["a2"]
    assert verdict["state"] == "kept" and verdict["named_by"] == 2
    assert "year-2007" in verdict["held_by"]


def test_a_year_whose_every_shot_is_named_keeps_one(tmp_path):
    """Two shots of one year, both named by both orders: neither is the year's only shot on
    its own, but taking both silences the year."""
    kept = polish_years(
        tmp_path,
        {
            "a1": ("2009-05-01T09:00:00", "a child on a swing"),
            "a2": ("2010-03-01T09:00:00", "filler: a child with a toy"),
            "a3": ("2010-07-01T09:00:00", "filler: a child at a table"),
            "a4": ("2011-05-01T09:00:00", "a child at the beach"),
        },
    )

    assert [a for a in kept if a in {"a2", "a3"}] and len(kept) == 3


def test_a_seat_takes_the_favourite_of_the_moment_it_picked(tmp_path):
    """The recorded picture leads the page and the picker takes it, but its moment's favourite is
    on the same page: the favourite wins its moment, as it did in the draft."""
    from tests.editorial_thin_fixtures import JUNK, START, Film, polish

    film = Film()
    film.tiers["S001"] = "maybe"
    film.draft.append(film.shot("d1", "S001", START, "people at a table"))
    film.draft.append(film.shot("d2", "S001", START.replace(day=3), JUNK))
    for asset, favourite in (("c-recorded", False), ("c-starred", True)):
        film.shot(
            asset, "S001", START.replace(day=2), "the first day at school", favourite=favourite
        )
        film.units[asset]["moment"] = "m-school"
    film.records["c-recorded"] = "the first day at school"

    _judge, _record, cut, newcomers = polish(tmp_path, film)

    assert newcomers == ["c-starred"]
    assert "c-recorded" not in {row["asset_id"] for row in cut}


def test_a_removed_shot_is_refilled_when_the_draft_already_runs_past_the_length(tmp_path):
    """Feb 2024 and the 2024 year (09-24): the draft ran past the length the polish measured
    against, so no seat opened for the shot the vote removed and the film lost a picture.
    A removal frees its own place; the refill takes it, and the film grows no longer."""
    from tests.editorial_thin_fixtures import JUNK, UNSTEADY, Film, draft_of, polish

    film = Film()
    draft_of(film, 8, flagged={2: JUNK, 5: UNSTEADY})

    _judge, record, cut, newcomers = polish(tmp_path, film, room=-10.0)

    assert len(newcomers) == 2
    assert len(cut) == len(film.draft)
    assert sum(row["seconds"] for row in cut) <= sum(row["seconds"] for row in film.draft)
    assert {slot["outcome"] for slot in record["slots"]} == {"seated"}


def test_a_removal_whose_story_has_nothing_left_is_refilled_from_the_films_other_stories(
    tmp_path,
):
    """The refill comes from the removed shot's own story, or else from the pool of the stories
    the film already holds, nearest in time first."""
    from datetime import timedelta

    from tests.editorial_thin_fixtures import JUNK, START, Film, polish

    film = Film()
    film.tiers.update({"S001": "maybe", "S002": "maybe", "S003": "maybe"})
    film.draft.append(film.shot("d1", "S001", START, "people at a table"))
    film.draft.append(film.shot("d2", "S002", START + timedelta(days=10), JUNK))
    film.draft.append(film.shot("d3", "S003", START + timedelta(days=20), "a walk in the park"))
    film.shot("far", "S001", START + timedelta(days=1), "people at a table again")
    film.shot("near", "S003", START + timedelta(days=12), "the park at dusk")

    _judge, record, _cut, newcomers = polish(tmp_path, film)

    assert newcomers == ["near"]
    assert [(slot["rule"], slot["outcome"]) for slot in record["slots"]] == [("vote-bad", "seated")]


def test_a_removal_with_nothing_left_to_refill_it_says_so(tmp_path):
    from tests.editorial_thin_fixtures import JUNK, START, Film, polish

    film = Film()
    film.tiers.update({"S001": "maybe", "S002": "maybe"})
    film.draft.append(film.shot("d1", "S001", START, "people at a table"))
    film.draft.append(film.shot("d2", "S002", START.replace(day=3), JUNK))

    _judge, record, cut, newcomers = polish(tmp_path, film)

    assert [row["asset_id"] for row in cut] == ["d1"] and not newcomers
    assert [(slot["rule"], slot["outcome"]) for slot in record["slots"]] == [
        ("vote-bad", "none available")
    ]


def test_a_removals_refill_the_gates_refuse_is_chosen_again_from_the_same_page(tmp_path):
    """The 2024 year (09-24): seats were lost to capture spacing with a whole page still
    unasked. A removal's seat picks once more when the gates refuse its first choice."""
    from datetime import timedelta

    from tests.editorial_thin_fixtures import JUNK, START, Film, polish

    film = Film()
    film.tiers["S001"] = "maybe"
    film.draft.append(film.shot("d1", "S001", START, "people at a table"))
    film.draft.append(film.shot("d2", "S001", START + timedelta(days=1), JUNK))
    film.shot("close", "S001", START + timedelta(minutes=2), "people at a table, closer")
    film.units["close"]["moment"] = "m-d1"
    film.shot("fine", "S001", START + timedelta(hours=3), "people in the garden")

    _judge, record, _cut, newcomers = polish(tmp_path, film)

    assert newcomers == ["fine"]
    assert [slot["outcome"] for slot in record["slots"]] == ["seated"]


def test_a_shot_the_vote_removed_is_not_refilled_from_its_own_moment(tmp_path):
    """April 2021 (09-25): the vote removed a cat at a sink, and its refill was the frame taken
    three seconds earlier. The vote judged the moment; another frame of it adds nothing either."""
    from datetime import timedelta

    from tests.editorial_thin_fixtures import JUNK, START, Film, polish

    film = Film()
    film.tiers["S001"] = "maybe"
    film.draft.append(film.shot("d1", "S001", START, "people at a table"))
    film.draft.append(film.shot("d2", "S001", START + timedelta(days=1), JUNK))
    film.shot("twin", "S001", START + timedelta(days=1, seconds=-3), "the same worktop")
    film.units["twin"]["moment"] = "m-d2"
    film.shot("other", "S001", START + timedelta(days=2), "people in the garden")

    _judge, _record, _cut, newcomers = polish(tmp_path, film)

    assert newcomers == ["other"]


def test_a_refill_that_repeats_a_scene_the_cut_holds_is_refused_and_chosen_again(tmp_path):
    """April 2021 (09-25): two refills repeated a scene the cut already held, and the final
    duplicate review took them out later with nothing in their place."""
    from datetime import timedelta

    import numpy as np

    from tests.editorial_thin_fixtures import JUNK, START, Film, polish

    film = Film()
    film.tiers["S001"] = "maybe"
    film.draft.append(film.shot("d1", "S001", START, "a beach at noon"))
    film.draft.append(film.shot("d2", "S001", START + timedelta(days=1), JUNK))
    film.shot("again", "S001", START + timedelta(hours=5), "the same beach")
    film.shot("fresh", "S001", START + timedelta(hours=9), "people in the garden")
    beach, garden = np.array([1.0, 0.0]), np.array([0.0, 1.0])
    prints = {"d1": beach, "again": beach, "fresh": garden, "d2": garden}

    _judge, record, _cut, newcomers = polish(tmp_path, film, scene_print=prints.get)

    assert newcomers == ["fresh"]
    assert [slot["outcome"] for slot in record["slots"]] == ["seated"]


def test_a_removals_refill_the_vote_revokes_is_chosen_again(tmp_path):
    """April 2021 (09-25): the vote re-check revoked a removal's refill, and the film kept the
    hole. A removal's seat picks once more from what is left of its page."""
    from datetime import timedelta

    from tests.editorial_thin_fixtures import JUNK, START, Film, polish

    film = Film()
    film.tiers["S001"] = "maybe"
    film.draft.append(film.shot("d1", "S001", START, "people at a table"))
    film.draft.append(film.shot("d2", "S001", START + timedelta(days=1), JUNK))
    film.shot("filler", "S001", START + timedelta(hours=5), f"{JUNK} again")
    film.shot("fine", "S001", START + timedelta(hours=9), "people in the garden")

    _judge, record, cut, newcomers = polish(tmp_path, film)

    assert newcomers == ["fine"]
    assert record["revoked_by_the_fit_check"] == ["filler"]
    assert len(cut) == 2


def test_two_removals_in_a_story_with_one_picture_left_both_refill_from_the_pool(tmp_path):
    """June 2023 (09-25): a removal's page held one picture, another seat took it, and the seat
    ended 'none available' while the film's other stories still had pictures."""
    from datetime import timedelta

    from tests.editorial_thin_fixtures import JUNK, START, Film, polish

    film = Film()
    film.tiers.update({"S001": "maybe", "S002": "maybe"})
    film.draft.append(film.shot("d1", "S001", START, "people at a table"))
    film.draft.append(film.shot("d2", "S001", START + timedelta(days=1), JUNK))
    film.draft.append(film.shot("d3", "S001", START + timedelta(days=2), f"{JUNK}, later"))
    film.draft.append(film.shot("d4", "S002", START + timedelta(days=4), "a walk in the park"))
    film.shot("spare", "S001", START + timedelta(days=1, hours=5), "people in the garden")
    film.shot("near", "S002", START + timedelta(days=3), "the park at dusk")

    _judge, record, _cut, newcomers = polish(tmp_path, film)

    assert sorted(newcomers) == ["near", "spare"]
    assert {slot["outcome"] for slot in record["slots"]} == {"seated"}
