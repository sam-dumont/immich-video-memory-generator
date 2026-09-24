"""A no-model film does not pad itself with pictures nothing vouches for and that show nothing.

The quiet month below has four shots. Two carry an indicator of their own, one is a plain still
of a place, and one is a plain still the frame head reads as a lone object: the last is filler
nobody can vouch for, so the film goes one shot short instead of keeping the guess.
"""

from __future__ import annotations

import pytest

from immich_memories.analysis.editorial_unvouched_filler import (
    FillerEvidence,
    drop_unvouched_filler,
)


def _shot(asset_id: str, *, kind: str = "still", favourite: bool = False) -> dict:
    return {"asset_id": asset_id, "kind": kind, "favourite": favourite, "seconds": 3.5}


def _evidence(frame_kinds, *, people=(), banked=(), protected=()):
    return FillerEvidence(
        frame_kind_of=frame_kinds.get,
        known_person=lambda asset: asset in people,
        vouched_by_bank=lambda asset: asset in banked,
        protected=frozenset(protected),
    )


def test_a_plain_still_the_heads_read_as_a_lone_object_leaves_and_nothing_takes_its_place():
    film = [
        _shot("fav", favourite=True),
        _shot("clip", kind="video"),
        _shot("place"),
        _shot("object"),
    ]
    frame_kinds = {
        "fav": "lone_everyday_object",
        "clip": "screen_or_document",
        "place": "place_or_scenery",
        "object": "lone_everyday_object",
    }

    kept, dropped = drop_unvouched_filler(film, _evidence(frame_kinds))

    assert [c["asset_id"] for c in kept] == ["fav", "clip", "place"]
    assert [c["asset_id"] for c in dropped] == ["object"]


@pytest.mark.parametrize(
    ("shot", "vouched"),
    [
        (_shot("object", kind="live-motion"), {}),
        (_shot("object"), {"people": ["object"]}),
        (_shot("object"), {"banked": ["object"]}),
        (_shot("object"), {"protected": ["object"]}),
    ],
    ids=["motion-plays", "known-person", "banked-standing", "owner-ticked"],
)
def test_any_indicator_keeps_a_picture_whatever_the_heads_read(shot, vouched):
    kept, dropped = drop_unvouched_filler(
        [shot], _evidence({"object": "lone_everyday_object"}, **vouched)
    )

    assert kept == [shot]
    assert dropped == []


def test_a_picture_the_frame_head_never_read_is_not_called_empty():
    kept, dropped = drop_unvouched_filler([_shot("unread")], _evidence({}))

    assert [c["asset_id"] for c in kept] == ["unread"]
    assert dropped == []


def _planned_quiet_month(tmp_path, *, frame_kind: str):
    """A rules-read month of five home days, three moments a day: one starred, two plain stills
    the frame head reads as `frame_kind`. The minute budget holds more shots than the stars."""
    from dataclasses import replace
    from datetime import date

    from immich_memories.analysis.editorial_rule_reader import NoModelJudge, RuleStructureReader
    from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
    from immich_memories.analysis.editorial_structure_planner import plan_structure
    from tests.editorial_film_fixtures import film_source, home_days

    days = [replace(day, moments=3) for day in home_days(date(2030, 2, 3), 5, step=2)]
    source = film_source(tmp_path, days, seconds=60, span=(date(2030, 2, 1), date(2030, 2, 28)))
    for asset_id, row in list(source.audience_annotations.items()):
        starred = asset_id.split("-")[1] == "m0"
        source.assets[asset_id].is_favorite = starred
        heads = (*row.heads, ("frame_kind", "people_moment" if starred else frame_kind))
        source.audience_annotations[asset_id] = replace(row, heads=heads)
    plan = plan_structure(
        source,
        StructurePlannerPorts(
            judge=NoModelJudge(),
            thumbnail_hash=lambda _asset: None,
            rules=RuleStructureReader(source),
        ),
    ).plan
    shots = [c["asset_id"] for c in plan["carriers"]]
    return shots, [a for a in shots if source.assets[a].is_favorite]


def test_the_rules_draft_goes_short_rather_than_fill_with_screens_nothing_vouches_for(tmp_path):
    shots, starred = _planned_quiet_month(tmp_path, frame_kind="screen_or_document")

    assert shots == starred
    assert len(starred) == 5


def test_the_same_month_keeps_its_plain_stills_when_they_show_a_place(tmp_path):
    shots, starred = _planned_quiet_month(tmp_path, frame_kind="place_or_scenery")

    assert len(starred) == 5
    assert len(shots) > len(starred)
