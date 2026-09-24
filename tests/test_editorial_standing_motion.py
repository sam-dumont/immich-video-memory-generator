"""A moving picture stands on its score like any other; motion never bypasses a zero."""

import pytest

from immich_memories.analysis.editorial_story_standing import StandingGate
from immich_memories.analysis.editorial_structure_lines import UnitLines

CLIP = {"asset_id": "clip", "kind": "video", "raw_seconds": 7.0, "members": ["clip"]}


def admission(unit, line, *, score, pictures=7):
    lines = UnitLines({"clip": line})
    gate = StandingGate(
        lambda _asset: score,
        line_of=lambda _asset: lines.line(unit),
        life=lambda _asset: lines.shows_life(unit),
        unit_by_asset={"clip": ("E1", unit)},
        pictures_of={"K01": pictures},
    )
    gate.ensure(["clip"])
    return gate


@pytest.mark.parametrize("kind", ["video", "live-motion"])
def test_motion_scored_zero_cannot_bypass_standing_in_a_major_story(kind):
    gate = admission(
        CLIP | {"kind": kind}, "2022-01-01 | An empty room with tiled floors.", score=0
    )

    assert not gate.stands("clip", "major", "K01")


@pytest.mark.parametrize("kind", ["video", "live-motion"])
@pytest.mark.parametrize("pictures", [1, 7])
def test_motion_that_stands_stands_when_its_still_has_no_people(kind, pictures):
    # A Live Photo carries a motion sentence only once its companion measured as moving.
    unit = CLIP | {"kind": kind, "residual": 2.4 if kind == "live-motion" else None}
    line = "2022-01-01 | An empty diving board above a pool."

    assert admission(unit, line, score=2, pictures=pictures).stands("clip", "major", "K01")
