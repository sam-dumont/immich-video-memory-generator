"""Standing read from facts alone: objects and useless stuff out, people and animals in."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_rule_reader import RuleStructureReader
from immich_memories.analysis.editorial_standing_facts import carries_nothing, names_someone_alive

LONE_OBJECT = {"frame_kind": "lone_everyday_object", "people": "one"}

# -- no caption: the heads and the pixel warnings decide ----------------------------------------


@pytest.mark.parametrize(
    "kind", ["empty_room_ceiling_or_floor", "accidental_or_blurred_frame", "lone_everyday_object"]
)
def test_a_frame_of_nothing_with_nobody_in_it_carries_nothing(kind):
    assert carries_nothing({"frame_kind": kind, "people": "one"}, "")


def test_children_in_a_frame_the_head_calls_a_lone_object_pull_it_back():
    assert not carries_nothing(LONE_OBJECT | {"children": "yes"}, "")


def test_an_animal_the_activity_head_sees_is_never_refused():
    assert not carries_nothing(LONE_OBJECT | {"activity": "animal-nature"}, "")


def test_a_record_the_document_head_also_reads_as_a_document_carries_nothing():
    heads = {"frame_kind": "meaningful_record", "doc_docling": "full_page_image"}

    assert carries_nothing(heads, "")
    assert not carries_nothing({"frame_kind": "meaningful_record"}, "")


@pytest.mark.parametrize("warning", ["SOFT (blurry)", "DARK", "BLOWN OUT", "SOFT (blurry) | DARK"])
def test_a_pixel_warning_alone_never_refuses_a_people_moment(warning):
    heads = {"frame_kind": "people_moment", "people": "two"}

    assert not carries_nothing(heads, f"2024-02-01 12:00 | A family at the table. | {warning}")


def test_a_blurred_body_part_close_up_carries_nothing_where_a_sharp_one_does_not():
    heads = {"frame_kind": "body_part_closeup", "people": "one"}

    assert carries_nothing(heads, "2024-02-01 12:00 | A hand. | SOFT (blurry)")
    assert not carries_nothing(heads, "2024-02-01 12:00 | A hand.")


def test_a_bank_with_no_frame_kind_row_reads_a_document_as_before_eligibility_does():
    """The document and screen heads keep a frame out of the carrier pool on their own; for
    standing they are evidence, not a verdict."""
    assert not carries_nothing({"doc_docling": "screenshot_from_computer", "people": "two"}, "")
    assert carries_nothing(
        {"frame_kind": "screen_or_document", "doc_docling": "screenshot_from_computer"}, ""
    )


# -- a living subject needs a face ----------------------------------------------------------------

SCREENED_MOMENT = {"frame_kind": "people_moment", "doc_docling": "screenshot", "screen": "yes"}


def test_a_people_moment_with_no_face_is_weighed_rather_than_stood_free():
    """The frame head calls legs, feet or a back a people moment too; only a face vouches for it."""
    line = "2024-02-01 12:00 | BLOWN OUT"

    assert not carries_nothing(SCREENED_MOMENT, line)
    assert not carries_nothing(SCREENED_MOMENT, line, face=True)
    assert carries_nothing(SCREENED_MOMENT, line, face=False)


def test_a_dark_people_moment_is_weighed_like_a_soft_one():
    """Dark is no reason to refuse a face, and no reason to skip counting either."""
    assert not carries_nothing(SCREENED_MOMENT, "2024-02-01 23:00 | BLOWN OUT", face=True)
    assert carries_nothing(SCREENED_MOMENT, "2024-02-01 23:00 | DARK | BLOWN OUT", face=True)


# -- with the ingest caption ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "caption",
    [
        "A baby lies on a blanket.",
        "A woman holds a cup of coffee.",
        "A dog sleeps on the sofa.",
        "Two horses graze in a field.",
    ],
)
def test_a_caption_that_names_someone_alive_is_never_refused(caption):
    assert not carries_nothing(
        LONE_OBJECT | {"frame_kind": "empty_room_ceiling_or_floor"}, "", caption
    )


@pytest.mark.parametrize(
    "caption",
    [
        "A stuffed bear sits on a shelf.",
        "A statue of a man stands in a square.",
        "Baby clothes are folded on a bed.",
        "A dog bowl filled with water.",
    ],
)
def test_a_likeness_of_a_living_thing_or_a_thing_made_for_one_is_an_object(caption):
    assert not names_someone_alive(caption)


@pytest.mark.parametrize(
    "caption",
    [
        "A smartwatch displaying the time is placed on a wooden table.",
        "A pair of shoes and a bottle placed on the floor.",
        "A white plate of food on a dining table.",
    ],
)
def test_a_lone_object_the_caption_says_is_an_object_carries_nothing(caption):
    assert carries_nothing(LONE_OBJECT, "", caption)


def test_a_place_the_caption_names_without_anyone_in_it_still_stands():
    heads = {"frame_kind": "place_or_scenery", "people": "one"}

    assert not carries_nothing(heads, "", "A lake at sunset with mountains in the distance.")


def test_a_caption_naming_a_person_keeps_the_picture_only_when_a_face_is_in_it():
    caption = "A person holds a phone displaying a game over a table."

    assert not carries_nothing(LONE_OBJECT, "", caption, face=True)
    assert not carries_nothing(LONE_OBJECT, "", caption)
    assert carries_nothing(LONE_OBJECT, "", caption, face=False)


def test_an_animal_the_caption_names_needs_no_face():
    caption = "A dog next to a phone displaying a game on a table."

    assert not carries_nothing(LONE_OBJECT, "", caption, face=False)


def test_a_shelf_of_goods_is_an_object_however_the_frame_head_saw_it():
    heads = {"frame_kind": "place_or_scenery", "people": "none"}

    assert carries_nothing(heads, "", "A shelf displaying various products and bottles.")
    assert not carries_nothing(heads, "", "A woman looks at products on a shelf.")


def test_someone_the_people_head_saw_without_a_face_counts_toward_nothing():
    heads = {"frame_kind": "lone_everyday_object", "people": "one"}

    assert not carries_nothing(heads, "", "A table by a window.", face=True)
    assert carries_nothing(heads, "", "A table by a window.", face=False)


def test_a_library_whose_faces_were_never_read_keeps_the_table_fitted_without_them():
    """The face-aware table lowers the other points because the missing face carries some of
    the weight; with no face facts at all, the table fitted without them decides."""
    heads = {"frame_kind": "lone_everyday_object", "people": "one"}

    assert carries_nothing(heads, "", "A table by a window.")


# -- the rules reader -----------------------------------------------------------------------------


def _reader(*, favourite=False, line="", description=None, audience="sendable", **heads):
    source = SimpleNamespace(
        assets={"a": SimpleNamespace(is_favorite=favourite, people=())},
        audience_annotations={
            "a": SimpleNamespace(heads=tuple(heads.items()), description=description)
        },
        annotations={"a": line},
        intent=SimpleNamespace(product="month"),
        audience=audience,
        owner_required_asset_ids=(),
    )
    return RuleStructureReader(source)


def test_the_rules_reader_refuses_what_the_facts_say_carries_nothing():
    assert _reader(frame_kind="lone_everyday_object", people="one").standing("a") == 0


def test_the_rules_reader_no_longer_refuses_a_dark_or_soft_family_picture():
    line = "2024-02-01 21:00 | Two children at a birthday cake. | SOFT (blurry) | DARK"

    assert _reader(frame_kind="people_moment", people="two", line=line).standing("a") == 2


def test_the_rules_reader_reads_the_caption_when_the_picture_has_one():
    reader = _reader(
        frame_kind="lone_everyday_object", people="one", description="A cat on a chair."
    )

    assert reader.standing("a") == 2


def test_a_favourite_stands_whatever_the_facts_say():
    reader = _reader(favourite=True, frame_kind="empty_room_ceiling_or_floor", people="none")

    assert reader.standing("a") == 2


def test_the_rules_reader_takes_a_picture_with_no_face_in_a_library_whose_faces_it_read():
    """Immich put nobody on this picture while it recognised someone elsewhere in the scope."""
    line = "2024-02-01 | BLOWN OUT"
    face_elsewhere = SimpleNamespace(is_favorite=False, people=[SimpleNamespace(id="p1", name="")])
    assert _reader(line=line, **SCREENED_MOMENT).standing("a") >= 1

    reader = _reader(line=line, **SCREENED_MOMENT)
    reader.source.assets["b"] = face_elsewhere

    assert reader.standing("a") == 0
