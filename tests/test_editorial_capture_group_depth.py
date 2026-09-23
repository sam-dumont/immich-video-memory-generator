"""A picture that cannot carry a frame must not spend one of its moment's rungs.

A short film deepens the moments it already shows, up to three frames a moment. The model
ranks a moment's members, so its ladder walks the top three by position. Ranked by capture
facts alone, position says little: an eight-picture moment whose third-ranked frame fails
the standing gate was shipping two frames and leaving five usable ones behind.
"""

from immich_memories.analysis.editorial_story_carriers import CarrierAdmission
from immich_memories.analysis.editorial_story_lookalike import LookAlikeCheck
from immich_memories.analysis.editorial_story_shortlist import DepictedChoice
from immich_memories.analysis.editorial_story_slots import PartitionedSlots
from immich_memories.analysis.editorial_story_standing import StandingGate

MEMBERS = ("first", "refused", "third", "fourth")
STANDING = {"first": 2, "refused": 0, "third": 2, "fourth": 2}


def _units():
    return {
        asset: (
            "F01",
            {
                "asset_id": asset,
                "moment": "M01",
                "taken": f"2022-08-13T10:0{index}:00",
                "favourite": False,
                "kind": "still",
                "seconds": 4.0,
            },
        )
        for index, asset in enumerate(MEMBERS)
    }


def _admission(*, mechanical, slots=8):
    unit_by_asset = _units()
    story = {
        "key": "S001",
        "title": "A long moment",
        "weight": "minor",
        "gate": "remarkable",
        "seen": {"favourites": 0},
        "purpose": "",
    }

    def never(*_args, **_kwargs):
        raise AssertionError("the depth ladder asked a model")

    judge = type("Judge", (), {"calls": [], "ask": staticmethod(never)})()
    gate = StandingGate(
        judge,
        line_of=lambda a: f"line for {a}",
        life=lambda _a: False,
        unit_by_asset=unit_by_asset,
        pictures_of={"S001": len(MEMBERS)},
        bank=None,
        save=None,
        calls={"standing_rounds": 0},
        score_of=STANDING.__getitem__,
    )
    choice = DepictedChoice(
        key="M01:cg",
        episode="S001",
        taken="2022-08-13T10:00:00",
        content="capture group",
        primary=MEMBERS[0],
        alternatives=list(MEMBERS[1:]),
    )
    return CarrierAdmission(
        judge,
        stories=[story],
        choices_of={"S001": [choice]},
        unit_by_asset=unit_by_asset,
        anchor_label={"F01": "A long moment"},
        parts=PartitionedSlots(unit_by_asset),
        gate=gate,
        line_of=lambda a: f"line for {a}",
        life=lambda _a: False,
        excluded={},
        kind_marker=lambda _c: "",
        motion_line=None,
        contract="",
        record=lambda _name, _value: None,
        slots=slots,
        calls={"pick_calls": 0, "standing_rounds": 0},
        mechanical_picks=mechanical,
        # Two frames of one moment never look alike here: this test is about the walk.
        lookalike=LookAlikeCheck(lambda _candidate, _keeper: False, slots=slots),
    )


def test_a_model_ladder_walks_the_moment_s_top_three_by_position():
    admission = _admission(mechanical=False)

    admission.run()

    assert [c["asset_id"] for c in admission.carriers] == ["first", "third"]


def test_the_rules_ladder_skips_a_picture_that_cannot_carry_a_frame():
    admission = _admission(mechanical=True)

    admission.run()

    assert [c["asset_id"] for c in admission.carriers] == ["first", "third", "fourth"]


def test_a_moment_never_carries_more_than_three_frames():
    admission = _admission(mechanical=True, slots=8)
    admission.gate.scores["refused"] = 2

    admission.run()

    assert len(admission.carriers) == 3


def test_a_film_that_has_filled_its_slots_takes_no_second_pick_of_a_group():
    admission = _admission(mechanical=True, slots=1)

    admission.run()

    assert [c["asset_id"] for c in admission.carriers] == ["first"]
