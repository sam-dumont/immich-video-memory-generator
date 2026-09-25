"""A shot of a body part with no face (legs, feet, shoes, hands alone) does not stand.

The owner's own rule, on top of the points tables: a favourite or a ticked picture still
stands, a body part with a face on it is a person, and an animal is never a body part.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_rule_reader import RuleStructureReader

FACE_ELSEWHERE = SimpleNamespace(is_favorite=False, people=[SimpleNamespace(id="p1", name="")])
FEET = "A person is standing on a tiled floor with their feet bare and hands clasped together."


def _reader(*, favourite=False, description=None, ticked=(), **heads):
    source = SimpleNamespace(
        assets={
            "a": SimpleNamespace(is_favorite=favourite, people=[]),
            # Immich recognised somebody in this scope, so an empty face list here means none.
            "b": FACE_ELSEWHERE,
        },
        audience_annotations={
            "a": SimpleNamespace(heads=tuple(heads.items()), description=description)
        },
        annotations={"a": ""},
        intent=SimpleNamespace(product="month"),
        audience="sendable",
        owner_required_asset_ids=frozenset(ticked),
    )
    return RuleStructureReader(source)


def test_the_body_part_head_with_no_face_does_not_stand():
    assert _reader(frame_kind="body_part_closeup", people="one").standing("a") == 0


@pytest.mark.parametrize(
    "caption",
    [FEET, "A pair of new running shoes on a wooden floor.", "Two hands holding a cup."],
)
def test_a_caption_of_a_body_part_or_footwear_with_no_face_does_not_stand(caption):
    reader = _reader(frame_kind="people_moment", people="one", description=caption)

    assert reader.standing("a") == 0


def test_a_body_part_with_a_face_on_it_is_a_person():
    reader = _reader(
        frame_kind="body_part_closeup",
        people="one",
        description=FEET,
    )
    reader.source.assets["a"].people = [SimpleNamespace(id="p2", name="Robin")]

    assert reader.standing("a") == 2


def test_an_animal_s_paws_are_not_a_body_part_shot():
    reader = _reader(frame_kind="people_moment", people="none", description="A dog's paws on sand.")

    assert reader.standing("a") >= 1


@pytest.mark.parametrize("vouched", [{"favourite": True}, {"ticked": ("a",)}])
def test_a_favourite_or_a_ticked_body_part_shot_stands(vouched):
    reader = _reader(frame_kind="body_part_closeup", people="one", description=FEET, **vouched)

    assert reader.standing("a") == 2
