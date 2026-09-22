"""A no-model film holds a picture of somebody longer than an empty scene."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_structure_budget import (
    MIN_CARRIER_SECONDS,
    NOMINAL_STILL_SECONDS,
)
from immich_memories.analysis.editorial_structure_material import (
    UnitBuilder,
    hold_the_ends,
    rules_still_seconds,
)
from immich_memories.config_loader import Config


def test_a_picture_of_somebody_is_held_longer_than_an_empty_scene():
    assert rules_still_seconds(favourite=False, known_people=True) > rules_still_seconds(
        favourite=False, known_people=False
    )


def test_a_starred_picture_is_held_longer_than_an_empty_scene():
    assert rules_still_seconds(favourite=True, known_people=False) > rules_still_seconds(
        favourite=False, known_people=False
    )


@pytest.mark.parametrize("favourite", [True, False])
@pytest.mark.parametrize("known_people", [True, False])
def test_every_hold_stays_inside_the_production_carrier_bounds(favourite, known_people):
    held = rules_still_seconds(favourite=favourite, known_people=known_people)

    assert MIN_CARRIER_SECONDS <= held <= NOMINAL_STILL_SECONDS + 1.0


def _carrier(asset_id, seconds=4.0, kind="still"):
    return {"asset_id": asset_id, "kind": kind, "seconds": seconds}


def test_the_first_and_last_shot_of_a_film_are_held_longer():
    carriers = [_carrier("a"), _carrier("b"), _carrier("c")]

    hold_the_ends(carriers)

    assert [c["seconds"] for c in carriers] == [4.5, 4.0, 4.5]


def test_the_ends_stay_inside_the_upper_bound():
    carriers = [_carrier("a", seconds=NOMINAL_STILL_SECONDS + 1.0), _carrier("b")]

    hold_the_ends(carriers)

    assert carriers[0]["seconds"] == NOMINAL_STILL_SECONDS + 1.0


def test_a_clip_keeps_the_length_its_own_material_gave_it():
    carriers = [_carrier("clip", seconds=6.0, kind="video"), _carrier("b")]

    hold_the_ends(carriers)

    assert carriers[0]["seconds"] == 6.0


def test_a_one_shot_film_is_lengthened_once():
    carriers = [_carrier("only")]

    hold_the_ends(carriers)

    assert carriers[0]["seconds"] == 4.5


def _asset(asset_id, *, favourite=False, people=()):
    return SimpleNamespace(
        id=asset_id,
        is_favorite=favourite,
        people=list(people),
        faces=[],
        duration_seconds=None,
        type=SimpleNamespace(value="IMAGE"),
        file_created_at=datetime(2024, 2, 7, 10, int(asset_id[-1]), tzinfo=UTC),
    )


def _holds(*, rules):
    assets = [_asset("empty0"), _asset("people1", people=["a"]), _asset("star2", favourite=True)]
    source = SimpleNamespace(
        assets={a.id: a for a in assets},
        motion_residuals={},
        speech_regions={},
        config=Config(),
        pixel_facts={a.id: (900.0, 118.0) for a in assets},
    )
    wall = SimpleNamespace(
        event_assets={"F01": [a.id for a in assets]},
        moment_of_asset={a.id: f"M{n}" for n, a in enumerate(assets)},
    )
    ports = SimpleNamespace(
        resolve_motion=None,
        thumbnail_hash=lambda _asset_id: None,
        rules=object() if rules else None,
    )
    builder = UnitBuilder(source, ports, wall, renderings={}, never_auto=set(), document_sources={})
    return {u["asset_id"]: u["seconds"] for u in builder.units_of("F01")}


def test_a_model_film_holds_every_still_for_the_nominal_four_seconds():
    assert set(_holds(rules=False).values()) == {NOMINAL_STILL_SECONDS}


def test_a_rules_film_holds_its_stills_by_what_they_show():
    holds = _holds(rules=True)

    assert holds["empty0"] == MIN_CARRIER_SECONDS
    assert holds["people1"] > holds["empty0"]
    assert holds["star2"] > holds["empty0"]
