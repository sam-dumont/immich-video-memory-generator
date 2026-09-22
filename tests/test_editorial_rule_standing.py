"""The rules standing gate may only test labels the shipped heads can produce.

The rule it replaces asked `venue == "home"` and four other labels no head has ever emitted,
so two of its branches were dead and it could never answer 0 from the heads at all.
"""

import json

import numpy as np
import pytest

from immich_memories.analysis.editorial_rule_reader import (
    OUTDOOR_LOCATION,
    PRIVATE_VENUES,
    PUBLIC_VENUES,
    RuleStructureReader,
    nothing_to_show,
)
from immich_memories.config_models_editorial_preparation import (
    EditorialPreparationConfig,
)


def _classes() -> dict[str, set[str]]:
    bundle = EditorialPreparationConfig().head_bundle_path
    meta = json.loads(str(np.load(bundle, allow_pickle=True)["meta"]))
    return {head["name"]: set(head["classes"]) for head in meta["heads"]}


def test_every_label_the_standing_rule_names_exists_in_the_shipped_bundle():
    classes = _classes()

    assert classes["venue"] >= PRIVATE_VENUES
    assert classes["venue"] >= PUBLIC_VENUES
    assert OUTDOOR_LOCATION in classes["location"]
    assert {"none", "undetermined"} <= classes["people"]
    assert "other" in classes["activity"]


def standing(**heads):
    return RuleStructureReader._visual_standing(heads, known_people=heads.pop("known", False))


def test_an_empty_private_interior_does_not_stand_on_its_own():
    assert standing(people="none", activity="other", venue="bedroom") == 0


def test_an_empty_room_that_is_not_private_still_stands_as_context():
    assert standing(people="none", activity="other", venue="other", location="indoor") == 1


def test_something_happening_in_a_private_interior_still_stands():
    assert standing(people="none", activity="celebration", venue="bedroom") == 2


@pytest.mark.parametrize(
    "heads",
    [
        {"people": "two"},
        {"known": True, "people": "none"},
        {"people": "undetermined", "activity": "sport-active"},
        {"people": "undetermined", "location": "outdoor"},
        {"people": "undetermined", "venue": "water"},
    ],
)
def test_people_an_activity_or_an_outdoor_place_stands(heads):
    assert standing(**heads) == 2


def test_a_picture_the_heads_say_nothing_about_is_context():
    assert standing() == 1


@pytest.mark.parametrize(
    "segment",
    [
        "worth 0.04; what=empty_room_ceiling_or_floor",
        "worth 0.09; what=accidental_or_blurred_frame",
        "worth 0.02; what=lone_everyday_object",
        "worth 0.07; what=body_part_closeup",
    ],
)
def test_the_picture_reader_can_answer_zero_where_the_heads_see_nothing_wrong(segment):
    assert nothing_to_show(f"2026-08-25 12:00 | A room. | picture: {segment}")


@pytest.mark.parametrize(
    "segment",
    [
        "worth 0.04; what=people_moment",
        "worth 0.55; what=empty_room_ceiling_or_floor",
        "worth 0.97; what=place_or_scenery",
    ],
)
def test_one_low_number_or_one_label_on_its_own_is_not_enough(segment):
    assert not nothing_to_show(f"2026-08-25 12:00 | A room. | picture: {segment}")


def test_a_line_with_no_picture_row_answers_nothing():
    assert not nothing_to_show("2026-08-25 12:00 | A room. | DARK")
