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


def _flagged_reader(audience: str) -> RuleStructureReader:
    from types import SimpleNamespace

    heads = (("nsfw_marqo", "yes"), ("people", "two"))
    source = SimpleNamespace(
        assets={"a": SimpleNamespace(is_favorite=False, people=())},
        audience_annotations={"a": SimpleNamespace(heads=heads)},
        shareability_flags={},
        annotations={"a": ""},
        intent=SimpleNamespace(product="month"),
        owner_required_asset_ids=(),
        audience=audience,
    )
    return RuleStructureReader(source)


def test_a_family_film_judges_an_exposure_flagged_picture_like_any_other():
    """The exposure hold says who may see the picture, and the household may; whether it stands
    is a separate question the heads answer the same way as for every other picture."""
    assert _flagged_reader("family").standing("a") == 2


def test_a_film_sent_outside_the_family_still_scores_an_exposure_flagged_picture_zero():
    assert _flagged_reader("sendable").standing("a") == 0
