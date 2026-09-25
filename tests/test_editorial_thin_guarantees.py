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
